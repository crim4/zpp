from data import ComparatorDict, MappedAst, Node, Proto, RealData, RealType, Symbol
from utils import error, has_infinite_recursive_layout, write_instance_content_to
import llvmlite.ir as ll

REALTYPE_PLACEHOLDER = RealType('placeholder_rt')
CSTRING_REALTYPE = RealType('ptr_rt', is_mut=False, type=RealType('u8_rt'))
STRING_REALTYPE = RealType('struct_rt', fields={
  'ptr': CSTRING_REALTYPE,
  'len': RealType('u64_rt')
})

class Generator:
  def __init__(self, map):
    self.maps = [map]
    self.output = ll.Module()
    self.fn_in_evaluation = ComparatorDict()
    self.fn_evaluated = ComparatorDict()
    self.namedtypes_in_evaluation = ComparatorDict()
    self.llvm_builders = [] # the last one is the builder in use
    self.ctx_types = []
    self.loops = []
    self.tmp_counter = 0
    self.str_counter = 0
    self.strings = {}
  
  @property
  def map(self) -> MappedAst:
    return self.maps[-1]

  @property
  def cur_fn(self) -> ll.Function:
    return list(self.fn_in_evaluation.values())[-1]
  
  @property
  def ctx(self):
    return self.ctx_types[-1]
  
  @property
  def allocas_builder(self) -> ll.IRBuilder:
    return self.cur_fn[2]

  @property
  def cur_builder(self) -> ll.IRBuilder:
    return self.llvm_builders[-1]
  
  @cur_builder.setter
  def cur_builder(self, new_builder):
    self.llvm_builders[-1] = new_builder
  
  @property
  def loop(self):
    return self.loops[-1]
  
  @property
  def inside_loop(self):
    return len(self.loop) > 0

  def push_loop(self, loop):
    # loop = (condition_checker_block, exit_block)
    self.loops.append(loop)

  def pop_loop(self):
    self.loops.pop()

  def make_proto(self, kind, **kwargs):
    return Proto(kind, **kwargs)

  def evaluate_builtin_type(self, name):
    try:
      return {
        'i8': RealType('i8_rt'),
        'i16': RealType('i16_rt'),
        'i32': RealType('i32_rt'),
        'i64': RealType('i64_rt'),

        'u8': RealType('u8_rt'),
        'u16': RealType('u16_rt'),
        'u32': RealType('u32_rt'),
        'u64': RealType('u64_rt'),

        'f32': RealType('f32_rt'),
        'f64': RealType('f64_rt'),

        'void': RealType('void_rt')
      }[name]
    except KeyError:
      pass

  def evaluate_named_type(self, type_node):
    sym = self.map.get_symbol(type_node.value, type_node.pos)

    if sym.kind == 'alias_sym':
      return sym.realtype

    check_sym_is_type(type_node.value, sym)

    key = id(sym)

    if key in self.namedtypes_in_evaluation:
      return self.namedtypes_in_evaluation[key]

    r = self.namedtypes_in_evaluation[key] = RealType('placeholder_rt')
    realtype = self.evaluate_type(sym.node.type, is_top_call=False)
    self.namedtypes_in_evaluation.remove_by_key(key)
    write_instance_content_to(realtype, r)

    return r

  def evaluate_type(self, type_node, is_top_call=True, allow_void_type=False):
    match type_node.kind:
      case 'id':
        t = self.evaluate_builtin_type(type_node.value)
        r = t if t is not None else self.evaluate_named_type(type_node)

        if r.is_void() and not allow_void_type:
          error('type `void` not allowed here', type_node.pos)
      
      case 'ptr_type_node':
        r = RealType('ptr_rt', is_mut=type_node.is_mut, type=self.evaluate_type(type_node.type, is_top_call=False))
      
      case 'array_type_node':
        length = self.evaluate_node(type_node.length, RealType('u64_rt'))
        self.expect_realdata_is_comptime_value(length, type_node.pos)
        self.expect_realdata_is_integer(length, type_node.pos)
        r = RealType('static_array_rt', length=length.value, type=self.evaluate_type(type_node.type, is_top_call=False))
      
      case 'struct_type_node':
        field_names = list(map(lambda field: field.name.value, type_node.fields))

        for i, field_name in enumerate(field_names):
          if field_names.count(field_name) > 1:
            error(f'field `{field_name}` is dupplicate', type_node.fields[i].name.pos)
        
        r = RealType(
          'struct_rt',
          fields={
            field.name.value: self.evaluate_type(field.type, is_top_call=False) for field in type_node.fields
          }
        )

      case _:
        raise NotImplementedError()
    
    if is_top_call and has_infinite_recursive_layout(r):
      error('type has infinite recursive layout', type_node.pos)

    return r

  def evaluate_fn_proto(self, fn_node):
    arg_types = [self.evaluate_type(arg.type) for arg in fn_node.args]
    ret_type = self.evaluate_type(fn_node.ret_type, allow_void_type=True)

    return self.make_proto(
      'fn_proto',
      arg_types=arg_types,
      ret_type=ret_type
    )

  def expect(self, cond, error_msg, pos):
    if cond:
      return

    error(error_msg, pos)

  def evaluate_num(self, num_tok, realtype_to_use=None):
    realtype = \
      realtype_to_use \
        if realtype_to_use is not None else \
          self.ctx if self.ctx.is_numeric() else REALTYPE_PLACEHOLDER

    value = (float if realtype.is_float() else int)(num_tok.value)
    
    return RealData(
      realtype,
      value=value,
      llvm_data=ll.Constant(self.convert_realtype_to_llvmtype(realtype), value)
    )

  def evaluate_fnum(self, fnum_tok, realtype_to_use=None):
    realtype = \
      realtype_to_use \
        if realtype_to_use is not None else \
          self.ctx if self.ctx.is_float() else REALTYPE_PLACEHOLDER

    value = float(fnum_tok.value)
    
    return RealData(
      realtype,
      value=value,
      llvm_data=ll.Constant(self.convert_realtype_to_llvmtype(realtype), value)
    )

  def evaluate_undefined(self, dd_node):
    realtype = self.ctx

    return RealData(
      realtype,
      value=0,
      llvm_data=ll.Constant(self.convert_realtype_to_llvmtype(realtype), ll.Undefined)
    )

  def evaluate_node(self, node, new_ctx, is_stmt=False, is_top_call=True):
    if new_ctx is not None:
      self.push_ctx(new_ctx)
    
    t = getattr(self, f'evaluate_{node.kind}')(node)

    if new_ctx is not None:
      self.pop_ctx()

    if not is_stmt and t.realtype.is_void():
      error('expression not allowed to be `void`', node.pos)
    
    if is_top_call and t.realtype.kind == 'placeholder_rt':
      error('expression has no clear type here', node.pos)

    return t

  def expect_realtype(self, rt1, rt2, pos):
    if rt1 == rt2:
      return
    
    error(f'expected `{rt1}`, found `{rt2}`', pos)

  def evaluate_null(self, null_tok):
    realtype = self.ctx if self.ctx.is_int() or self.ctx.is_ptr() else RealType('ptr_rt', is_mut=False, type=RealType('u8_rt'))
    llvm_data = ll.Constant(self.convert_realtype_to_llvmtype(realtype), None if realtype.is_ptr() else 0)
    
    return RealData(
      realtype,
      value=0,
      llvm_data=llvm_data
    )

  def evaluate_id(self, id_tok):
    sym = self.map.get_symbol(id_tok.value, id_tok.pos)
    llvm_data = self.llvm_load(self.cur_builder, sym.llvm_alloca, self.convert_realtype_to_llvmtype(sym.realtype))
    
    return RealData(
      sym.realtype,
      llvm_data=llvm_data
    )
  
  def generate_llvm_bin_for_int(self, realdata_left, op, realdata_right, realtype_resulting_from_cmp):
    is_signed = realdata_left.realtype.is_signed
    
    match op.kind:
      case '+': return self.cur_builder.add(realdata_left.llvm_data, realdata_right.llvm_data)
      case '-': return self.cur_builder.sub(realdata_left.llvm_data, realdata_right.llvm_data)
      case '*': return self.cur_builder.mul(realdata_left.llvm_data, realdata_right.llvm_data)
      case '/': return getattr(self.cur_builder, 'sdiv' if is_signed else 'udiv')(realdata_left.llvm_data, realdata_right.llvm_data)

      case '==' | '!=' | '<' | '>' | '<=' | '>=':
        return self.llvm_icmp(
          self.cur_builder,
          is_signed,
          op.kind,
          realdata_left.llvm_data,
          realdata_right.llvm_data,
          self.convert_realtype_to_llvmtype(realtype_resulting_from_cmp)
        )

      case _:
        raise NotImplementedError()
  
  def generate_llvm_bin_for_float(self, realdata_left, op, realdata_right, realtype_resulting_from_cmp):
    match op.kind:
      case '+': return self.cur_builder.fadd(realdata_left.llvm_data, realdata_right.llvm_data)
      case '-': return self.cur_builder.fsub(realdata_left.llvm_data, realdata_right.llvm_data)
      case '*': return self.cur_builder.fmul(realdata_left.llvm_data, realdata_right.llvm_data)
      case '/': return self.cur_builder.fdiv(realdata_left.llvm_data, realdata_right.llvm_data)

      case '==' | '!=' | '<' | '>' | '<=' | '>=':
        return self.llvm_fcmp(
          self.cur_builder,
          op.kind,
          realdata_left.llvm_data,
          realdata_right.llvm_data,
          self.convert_realtype_to_llvmtype(realtype_resulting_from_cmp)
        )

      case _:
        raise NotImplementedError()

  def generate_llvm_bin(self, realdata_left, op, realdata_right, realtype_resulting_from_cmp):
    if realdata_left.realtype.is_float():
      return self.generate_llvm_bin_for_float(realdata_left, op, realdata_right, realtype_resulting_from_cmp)
    
    return self.generate_llvm_bin_for_int(realdata_left, op, realdata_right, realtype_resulting_from_cmp)

  def compute_comptime_bin(self, realdata_left, op, realdata_right):
    match op.kind:
      case '+': return realdata_left.value + realdata_right.value
      case '-': return realdata_left.value - realdata_right.value
      case '*': return realdata_left.value * realdata_right.value
      case '/': return realdata_left.value / realdata_right.value

      case '==': return int(realdata_left.value == realdata_right.value)
      case '!=': return int(realdata_left.value != realdata_right.value)
      case '<': return int(realdata_left.value < realdata_right.value)
      case '>': return int(realdata_left.value > realdata_right.value)
      case '<=': return int(realdata_left.value <= realdata_right.value)
      case '>=': return int(realdata_left.value >= realdata_right.value)

      case _:
        raise NotImplementedError()
  
  def expect_realtype_are_compatible(self, rt1, rt2, pos):
    if rt1 == rt2:
      return
    
    error(f'types `{rt1}` and `{rt2}` are not compatible', pos)

  def expect_realdata_is_struct(self, realdata, pos):
    if realdata.realtype.is_struct():
      return
    
    error(f'expected struct expression, got `{realdata.realtype}`', pos)

  def expect_realdata_is_numeric(self, realdata, pos):
    if realdata.realtype.is_numeric():
      return
    
    error(f'expected numeric expression, got `{realdata.realtype}`', pos)

  def expect_realdata_is_indexable(self, realdata, pos):
    if realdata.realtype.is_ptr() or realdata.realtype.is_static_array():
      return
    
    error(f'expected indexable expression, got `{realdata.realtype}`', pos)

  def expect_realdata_is_comptime_value(self, realdata, pos):
    if realdata.is_comptime_value():
      return
    
    error(f'expected comptime expression', pos)

  def expect_realdata_is_integer(self, realdata, pos):
    if realdata.realtype.is_int():
      return
    
    error(f'expected integer expression, got `{realdata.realtype}`', pos)

  def ctx_if_int_or(self, alternative_rt):
    return self.ctx if self.ctx.is_int() else alternative_rt

  def evaluate_andor_node(self, bin_node):
    def restore_previous_state(previous_builder_maxindex, llvm_block_left_is_true, llvm_block_left_is_false, old_builder):
      self.cur_fn[1].remove_basic_block(llvm_block_left_is_true)
      self.cur_fn[1].remove_basic_block(llvm_block_left_is_false)
      self.cur_builder = old_builder

      if previous_builder_maxindex is None:
        return

      for _ in range(previous_builder_maxindex, len(self.cur_builder.block.instructions)):
        self.cur_builder.remove(self.cur_builder.block.instructions[-1])

    is_and = bin_node.op.kind == 'and'

    llvm_block_left_is_true = self.cur_fn[1].append_basic_block('left_is_true_branch' if is_and else 'left_is_false_branch')
    llvm_block_left_is_false = self.cur_fn[1].append_basic_block('left_is_false_branch' if is_and else 'left_is_true_branch')

    previous_builder_maxindex = len(self.cur_builder.block.instructions)
    realdata_left = self.evaluate_node(bin_node.left, self.ctx_if_int_or(RealType('u8_rt')), is_top_call=False)
    self.llvm_cbranch(
      self.cur_builder,
      realdata_left.llvm_data,
      llvm_block_left_is_true if is_and else llvm_block_left_is_false,
      llvm_block_left_is_false if is_and else llvm_block_left_is_true
    )

    old_builder = self.cur_builder
    llvm_block_phi_init = self.cur_builder.block
    self.cur_builder = ll.IRBuilder(llvm_block_left_is_true)
    realdata_right = self.evaluate_node(bin_node.right, self.ctx_if_int_or(RealType('u8_rt')), is_top_call=False)
    self.cur_builder.branch(llvm_block_left_is_false)

    if realdata_left.realtype_is_coercable():
      realdata_left.realtype = realdata_right.realtype

    if realdata_right.realtype_is_coercable():
      realdata_right.realtype = realdata_left.realtype
    
    self.expect_realdata_is_integer(realdata_left, bin_node.left.pos)
    self.expect_realdata_is_integer(realdata_right, bin_node.right.pos)
    self.expect_realtype_are_compatible(realdata_left.realtype, realdata_right.realtype, bin_node.pos)

    realtype = realdata_left.realtype
    llvm_type = self.convert_realtype_to_llvmtype(realtype)

    if realdata_left.is_comptime_value() and realdata_right.is_comptime_value():
      restore_previous_state(previous_builder_maxindex, llvm_block_left_is_true, llvm_block_left_is_false, old_builder)

      return self.evaluate_num(
        Node(
          'num',
          value=str(realdata_left.value and realdata_right.value) if is_and else str(realdata_left.value or realdata_right.value),
          pos=bin_node.pos
        ),
        realtype_to_use=realtype
      )
    elif realdata_left.is_comptime_value():
      if not is_and and realdata_left.value == 1:
        restore_previous_state(previous_builder_maxindex, llvm_block_left_is_true, llvm_block_left_is_false, old_builder)
        
        return RealData(
          realtype,
          value=1,
          llvm_data=ll.Constant(llvm_type, 1)
        )

      if is_and and realdata_left.value == 0:
        restore_previous_state(previous_builder_maxindex, llvm_block_left_is_true, llvm_block_left_is_false, old_builder)

        return RealData(
          realtype,
          value=0,
          llvm_data=ll.Constant(llvm_type, 0)
        )
    elif realdata_right.is_comptime_value():
      if not is_and and realdata_right.value == 1:
        self.cur_builder = ll.IRBuilder(llvm_block_left_is_false)

        return RealData(
          realtype,
          value=1,
          llvm_data=ll.Constant(llvm_type, 1)
        )

      if is_and and realdata_right.value == 0:
        self.cur_builder = ll.IRBuilder(llvm_block_left_is_false)

        return RealData(
          realtype,
          value=0,
          llvm_data=ll.Constant(llvm_type, 0)
        )

    self.cur_builder = ll.IRBuilder(llvm_block_left_is_false)

    llvm_data = self.llvm_phi(
      self.cur_builder, [
        (ll.Constant(llvm_type, 0 if is_and else 1), llvm_block_phi_init),
        (realdata_right.llvm_data, llvm_block_left_is_true)
      ],
      llvm_type
    )

    return RealData(
      realtype,
      llvm_data=llvm_data
    )

  def evaluate_bin_node(self, bin_node):
    if bin_node.op.kind in ['and', 'or']:
      return self.evaluate_andor_node(bin_node)

    get_resulting_realtype_of_bin_expr = lambda realdata: \
      realdata.realtype \
        if bin_node.op.kind not in ['==', '!=', '<', '>', '<=', '<='] else \
          self.ctx_if_int_or(RealType('u8_rt'))
  
    realdata_left = self.evaluate_node(bin_node.left, REALTYPE_PLACEHOLDER, is_top_call=False)
    realdata_right = self.evaluate_node(bin_node.right, REALTYPE_PLACEHOLDER, is_top_call=False)

    if realdata_left.realtype_is_coercable():
      realdata_left.realtype = realdata_right.realtype

    if realdata_right.realtype_is_coercable():
      realdata_right.realtype = realdata_left.realtype

    self.expect_realdata_is_numeric(realdata_left, bin_node.left.pos)
    self.expect_realdata_is_numeric(realdata_right, bin_node.right.pos)

    if realdata_left.is_comptime_value() and realdata_right.is_comptime_value():
      self.expect_realtype_are_compatible(realdata_left.realtype, realdata_right.realtype, bin_node.pos)
      is_float = realdata_left.realtype.is_float()

      return (self.evaluate_fnum if is_float else self.evaluate_num)(
        Node(
          'fnum' if is_float else 'num',
          value=str(self.compute_comptime_bin(realdata_left, bin_node.op, realdata_right)),
          pos=bin_node.pos
        ),
        realtype_to_use=get_resulting_realtype_of_bin_expr(realdata_left)
      )
    
    if realdata_left.is_comptime_value():
      new_realtype = realdata_left.realtype = realdata_right.realtype
      realdata_left.llvm_data = ll.Constant(self.convert_realtype_to_llvmtype(new_realtype), realdata_left.llvm_data.constant)
    
    if realdata_right.is_comptime_value():
      # if bin_node.op.kind in ['/', '%'] and realdata_right.value == 0:
      #   error('dividing by `0`', bin_node.pos)

      new_realtype = realdata_right.realtype = realdata_left.realtype
      realdata_right.llvm_data = ll.Constant(self.convert_realtype_to_llvmtype(new_realtype), realdata_right.llvm_data.constant)

    self.expect_realtype_are_compatible(realdata_left.realtype, realdata_right.realtype, bin_node.pos)

    realtype = get_resulting_realtype_of_bin_expr(realdata_left)
    llvm_data = self.generate_llvm_bin(realdata_left, bin_node.op, realdata_right, realtype)

    return RealData(
      realtype,
      llvm_data=llvm_data
    )
  
  def evaluate_pass_node_stmt(self, pass_node):
    pass
  
  def is_bitcast(self, source_rt, target_rt):
    return source_rt.is_ptr() and target_rt.is_ptr()

  def is_numeric_cast(self, source_rt, target_rt):
    return source_rt.is_numeric() and target_rt.is_numeric()

  def is_ptrint_cast(self, source_rt, target_rt):
    return \
      (source_rt.is_numeric() and target_rt.is_ptr()) or \
      (source_rt.is_ptr() and target_rt.is_numeric())

  def make_bitcast(self, realdata_expr, target_rt):
    realdata_expr.realtype = target_rt
    realdata_expr.llvm_data = self.cur_builder.bitcast(
      realdata_expr.llvm_data,
      self.convert_realtype_to_llvmtype(target_rt)
    )
  
  def evaluate_dot_node(self, dot_node):
    instance_realdata = self.evaluate_node(dot_node.left_expr, REALTYPE_PLACEHOLDER)
    field_name = dot_node.right_expr.value

    self.expect_realdata_is_struct(instance_realdata, dot_node.pos)

    if field_name not in instance_realdata.realtype.fields:
      error(f'struct `{instance_realdata.realtype}` has no field `{field_name}`', dot_node.pos)

    field_index = list(instance_realdata.realtype.fields.keys()).index(field_name)
    realtype = instance_realdata.realtype.fields[field_name]

    if isinstance(instance_realdata.llvm_data, ll.LoadInstr):
      self.cur_builder.remove(instance_realdata.llvm_data)
      instance_realdata.llvm_data = instance_realdata.llvm_data.operands[0]

      llvm_data = self.llvm_load(self.cur_builder,
        self.llvm_gep(
          self.cur_builder,
          instance_realdata.llvm_data,
          [ll.Constant(ll.IntType(32), 0), ll.Constant(ll.IntType(32), field_index)],
          True
        ),
        self.convert_realtype_to_llvmtype(realtype)
      )
    else:
      resulting_llvm_type = self.convert_realtype_to_llvmtype(realtype)
      llvm_data = self.llvm_extract_value(self.cur_builder, instance_realdata.llvm_data, field_index, resulting_llvm_type)

    return RealData(
      realtype,
      llvm_data=llvm_data
    )
  
  def evaluate_index_node(self, index_node):
    instance_realdata = self.evaluate_node(index_node.instance_expr, REALTYPE_PLACEHOLDER)
    return self.internal_evaluate_index_node(index_node, instance_realdata)

  def internal_evaluate_index_node(self, index_node, instance_realdata):
    index_realdata = self.evaluate_node(index_node.index_expr, RealType('u64_rt'))

    self.expect_realdata_is_indexable(instance_realdata, index_node.instance_expr.pos)
    self.expect_realdata_is_integer(index_realdata, index_node.index_expr.pos)

    indices = [index_realdata.llvm_data]

    if instance_realdata.realtype.is_static_array():
      self.cur_builder.remove(instance_realdata.llvm_data)
      instance_realdata.llvm_data = instance_realdata.llvm_data.operands[0]
      indices.insert(0, ll.Constant(ll.IntType(64), 0))

    llvm_data = self.llvm_load(
      self.cur_builder,
      self.llvm_gep(
        self.cur_builder,
        instance_realdata.llvm_data,
        indices,
        False
      ),
      self.convert_realtype_to_llvmtype(instance_realdata.realtype.type)
    )

    return RealData(
      instance_realdata.realtype.type,
      llvm_data=llvm_data
    )

  def evaluate_array_init_node(self, init_node):
    if self.ctx.is_static_array():
      realdatas = self.evaluate_array_init_nodes(init_node, self.ctx.type)
    else:
      realdatas = self.evaluate_array_init_nodes(init_node, REALTYPE_PLACEHOLDER)

    realtype = RealType('static_array_rt', length=len(init_node.nodes), type=realdatas[0].realtype)
    element_llvm_type = self.convert_realtype_to_llvmtype(realtype.type)
    llvm_data = ll.Constant(self.convert_realtype_to_llvmtype(realtype), ll.Undefined)

    for i, realdata in enumerate(realdatas):
      llvm_data = self.llvm_insert_value(self.cur_builder, llvm_data, realdata.llvm_data, i, element_llvm_type)

    return RealData(
      realtype,
      llvm_data=llvm_data
    )

  def evaluate_array_init_nodes(self, init_node, ctx_type_for_the_first_node):
    realdatas = []
    
    for i, node in enumerate(init_node.nodes):
      is_first_node = i == 0

      realdatas.append(rd := self.evaluate_node(
        node,
        ctx_type_for_the_first_node if is_first_node else realdatas[0].realtype
      ))

      if not is_first_node:
        self.expect_realtype(rd.realtype, realdatas[0].realtype, node.pos)

    return realdatas
  
  def evaluate_struct_init_node(self, init_node):
    field_names = list(map(lambda field: field.name.value, init_node.fields))
    ctx_realtypes = list(self.ctx.fields.values()) if self.ctx.is_struct() else []
    field_realdatas = [self.evaluate_node(field.expr, ctx_realtypes[i] if i < len(ctx_realtypes) else REALTYPE_PLACEHOLDER) for i, field in enumerate(init_node.fields)]
    field_realtypes = map(lambda realdata: realdata.realtype, field_realdatas)

    for i, field_name in enumerate(field_names):
      if field_names.count(field_name) > 1:
        error(f'field `{field_name}` is dupplicate', init_node.fields[i].name.pos)
    
    realtype = RealType('struct_rt', fields=dict(zip(field_names, field_realtypes)))
    llvm_data = ll.Constant(self.convert_realtype_to_llvmtype(realtype), ll.Undefined)

    for i, field_realdata in enumerate(field_realdatas):
      real_expected_value_llvm_type = self.convert_realtype_to_llvmtype(field_realdata.realtype)
      llvm_data = self.llvm_insert_value(self.cur_builder, llvm_data, field_realdata.llvm_data, i, real_expected_value_llvm_type)

    return RealData(
      realtype,
      llvm_data=llvm_data
    )

  def make_numeric_cast(self, realdata_expr, target_rt):
    source_bits = realdata_expr.realtype.bits
    target_bits = target_rt.bits
    llvm_target_type = self.convert_realtype_to_llvmtype(target_rt)
    is_signed = realdata_expr.realtype.is_signed
    is_signed_target_rt = target_rt.is_signed

    if realdata_expr.realtype == target_rt:
      return

    if realdata_expr.realtype.is_float() and target_rt.is_float():
      llvm_caster = self.cur_builder.fpext if source_bits < target_bits else self.cur_builder.fptrunc
    elif realdata_expr.realtype.is_float():
      llvm_caster = self.cur_builder.fptosi if is_signed_target_rt else self.cur_builder.fptoui
    elif target_rt.is_float():
      llvm_caster = self.cur_builder.sitofp if is_signed else self.cur_builder.uitofp
    else:
      llvm_caster = getattr(self.cur_builder, 'sext' if is_signed else 'zext') if source_bits < target_bits else self.cur_builder.trunc

    realdata_expr.realtype = target_rt
    realdata_expr.llvm_data = llvm_caster(
      realdata_expr.llvm_data,
      llvm_target_type
    )
  
  def evaluate_as_node(self, as_node):
    target_rt = self.evaluate_type(as_node.type)
    realdata_expr = self.evaluate_node(as_node.expr, target_rt)
    source_rt = realdata_expr.realtype

    if self.is_bitcast(source_rt, target_rt):
      self.make_bitcast(realdata_expr, target_rt)
    elif self.is_numeric_cast(source_rt, target_rt):
      self.make_numeric_cast(realdata_expr, target_rt)
    # elif self.is_ptrint_cast(source_rt, target_rt):
    #   pass
    else:
      error(f'invalid cast from `{source_rt}` to `{target_rt}`', as_node.pos)
    
    if realdata_expr.is_comptime_value():
      realdata_expr.realtype_is_coerced = None

    return realdata_expr

  def is_deref_node(self, node):
    return node.kind == 'unary_node' and node.op.kind == '*'

  def is_index_node(self, node):
    return node.kind == 'index_node'

  def expect_realdata_is_ptr(self, realdata, pos):
    if realdata.realtype.is_ptr():
      return
    
    error(f'expected pointer expression, got `{realdata.realtype}`', pos)

  def evaluate_unary_node(self, unary_node):
    match unary_node.op.kind:
      case '&':
        return self.evaluate_reference_node(unary_node)
      
      case 'not':
        return self.evaluate_not_node(unary_node)
    
      case '*':
        realdata_expr = self.evaluate_node(unary_node.expr, RealType('ptr_rt', is_mut=False, type=self.ctx))
        self.expect_realdata_is_ptr(realdata_expr, unary_node.expr.pos)

        return RealData(
          realdata_expr.realtype.type,
          llvm_data=self.llvm_load(self.cur_builder, realdata_expr.llvm_data, self.convert_realtype_to_llvmtype(realdata_expr.realtype.type))
        )
      
      case _:
        realdata_expr = self.evaluate_node(unary_node.expr, self.ctx)
        self.expect_realdata_is_numeric(realdata_expr, unary_node.expr.pos)

        if unary_node.op.kind != '-':
          return realdata_expr

        if realdata_expr.is_comptime_value():
          realdata_expr.value = -realdata_expr.value
          realdata_expr.llvm_data.constant = -realdata_expr.llvm_data.constant
        else:
          realdata_expr.llvm_data = self.cur_builder.neg(realdata_expr.llvm_data)

        return realdata_expr

  def evaluate_var_decl_node_stmt(self, var_decl_node):
    realtype = self.evaluate_type(var_decl_node.type)
    realdata = self.evaluate_node(var_decl_node.expr, realtype)

    self.expect_realtype(realtype, realdata.realtype, var_decl_node.expr.pos)

    llvm_alloca = self.allocas_builder.alloca(self.convert_realtype_to_llvmtype(realtype), name=var_decl_node.name.value)

    self.llvm_store(self.cur_builder, realdata.llvm_data, llvm_alloca)

    sym = Symbol(
      'local_var_sym',
      realtype=realtype,
      llvm_alloca=llvm_alloca
    )

    self.map.declare_symbol(var_decl_node.name.value, sym, var_decl_node.name.pos)

  def evaluate_if_node_stmt(self, if_node):
    has_else_branch = if_node.else_branch is not None
    
    llvm_block_if_branch = self.cur_fn[1].append_basic_block('if_branch_block')
    llvm_block_elif_condcheckers = [self.cur_fn[1].append_basic_block('elif_condchecker') for _ in if_node.elif_branches]
    llvm_block_elif_branches = [self.cur_fn[1].append_basic_block('elif_branch_block') for _ in if_node.elif_branches]
    llvm_block_else_branch = self.cur_fn[1].append_basic_block('else_branch_block') if has_else_branch else None
    llvm_exit_block = self.cur_fn[1].append_basic_block('exit_block')

    cond_rd = self.evaluate_node(if_node.if_branch.cond, RealType('u8_rt'))
    self.expect_realdata_is_integer(cond_rd, if_node.if_branch.cond.pos)

    false_br = llvm_block_elif_condcheckers[0] if len(llvm_block_elif_branches) > 0 else llvm_block_else_branch if has_else_branch else llvm_exit_block

    if cond_rd.is_comptime_value():
      self.cur_builder.branch(llvm_block_if_branch if cond_rd.value else false_br)
    else:
      self.llvm_cbranch(self.cur_builder, cond_rd.llvm_data, llvm_block_if_branch, false_br)

    self.push_sub_scope()
    self.push_builder(ll.IRBuilder(llvm_block_if_branch))
    has_terminator = self.evaluate_block(if_node.if_branch.body)
    self.fix_sub_scope_terminator(has_terminator, llvm_exit_block)
    self.pop_builder()
    self.pop_scope()

    for i, elif_branch in enumerate(if_node.elif_branches):
      self.push_builder(ll.IRBuilder(llvm_block_elif_condcheckers[i]))

      cond_rd = self.evaluate_node(elif_branch.cond, RealType('u8_rt'))
      self.expect_realdata_is_integer(cond_rd, elif_branch.cond.pos)

      false_br = llvm_block_elif_condcheckers[i + 1] if i + 1 < len(llvm_block_elif_branches) else llvm_block_else_branch if has_else_branch else llvm_exit_block

      if cond_rd.is_comptime_value():
        self.cur_builder.branch(llvm_block_elif_branches[i] if cond_rd.value else false_br)
      else:
        self.llvm_cbranch(self.cur_builder, cond_rd.llvm_data, llvm_block_elif_branches[i], false_br)

      self.pop_builder()
      
      self.push_sub_scope()
      self.push_builder(ll.IRBuilder(llvm_block_elif_branches[i]))
      has_terminator = self.evaluate_block(elif_branch.body)
      self.fix_sub_scope_terminator(has_terminator, llvm_exit_block)
      self.pop_builder()
      self.pop_scope()

    if has_else_branch:
      self.push_sub_scope()
      self.push_builder(ll.IRBuilder(llvm_block_else_branch))
      has_terminator = self.evaluate_block(if_node.else_branch.body)
      self.fix_sub_scope_terminator(has_terminator, llvm_exit_block)
      self.pop_builder()
      self.pop_scope()

    self.cur_builder = ll.IRBuilder(llvm_exit_block)
  
  def fix_sub_scope_terminator(self, has_terminator, llvm_exit_block):
    if has_terminator:
      return
    
    self.cur_builder.branch(llvm_exit_block)

  def evaluate_return_node_stmt(self, return_node):
    cur_fn_ret_type = self.cur_fn[0].ret_type

    if return_node.expr is None:
      self.expect_realtype(cur_fn_ret_type, RealType('void_rt'), return_node.pos)
      self.cur_builder.ret_void()
      return

    expr = self.evaluate_node(return_node.expr, cur_fn_ret_type)

    self.expect_realtype(cur_fn_ret_type, expr.realtype, return_node.expr.pos)

    self.llvm_ret(self.cur_builder, expr.llvm_data, self.convert_realtype_to_llvmtype(cur_fn_ret_type))
  
  def evaluate_generics_in_call(self, generic_type_nodes):
    return list(map(lambda node: self.evaluate_type(node), generic_type_nodes))

  def evaluate_call_node(self, call_node):
    fn = self.map.get_symbol(call_node.name.value, call_node.name.pos)
    check_sym_is_fn(call_node.name.value, fn)

    if len(call_node.generics) != len(fn.node.generics):
      error(f'expected `{len(fn.node.generics)}` generic args, got `{len(call_node.generics)}`', call_node.pos)

    if len(call_node.args) != len(fn.node.args):
      error(f'expected `{len(fn.node.args)}` args, got `{len(call_node.args)}`', call_node.pos)

    proto, llvmfn, _ = \
      self.gen_nongeneric_fn(fn) \
        if len(fn.node.generics) == 0 else \
          self.gen_generic_fn(fn, self.evaluate_generics_in_call(call_node.generics))

    realdata_args = []
    
    for i, arg_node in enumerate(call_node.args):
      realdata_args.append(realdata_arg := self.evaluate_node(arg_node, proto_arg_type := proto.arg_types[i]))
      self.expect_realtype(proto_arg_type, realdata_arg.realtype, arg_node.pos)

    llvm_args = list(map(lambda arg: arg.llvm_data, realdata_args))
    llvm_call = self.llvm_call(self.cur_builder, llvmfn, llvm_args)

    return RealData(
      proto.ret_type,
      llvm_data=llvm_call
    )

  def evaluate_while_node_stmt(self, while_node):
    llvm_block_check = self.cur_fn[1].append_basic_block('condcheck_block')
    llvm_block_loop = self.cur_fn[1].append_basic_block('loop_branch_block')
    llvm_block_exit = self.cur_fn[1].append_basic_block('exit_branch_block')

    self.cur_builder.branch(llvm_block_check)
    self.cur_builder = ll.IRBuilder(llvm_block_check)

    cond_rd = self.evaluate_node(while_node.cond, RealType('u8_rt'))
    self.expect_realdata_is_integer(cond_rd, while_node.cond.pos)

    if cond_rd.is_comptime_value():
      self.cur_builder.branch(llvm_block_loop if cond_rd.value else llvm_block_exit)
    else:
      self.llvm_cbranch(self.cur_builder, cond_rd.llvm_data, llvm_block_loop, llvm_block_exit)

    self.push_sub_scope()
    self.push_builder(ll.IRBuilder(llvm_block_loop))
    self.push_loop((llvm_block_check, llvm_block_exit))

    has_terminator = self.evaluate_block(while_node.body)
    self.fix_sub_scope_terminator(has_terminator, llvm_block_check)

    self.pop_loop()
    self.pop_builder()
    self.pop_scope()

    self.cur_builder = ll.IRBuilder(llvm_block_exit)

  def evaluate_true(self, node):
    return self.evaluate_num(
      Node('num', value='1', pos=node.pos),
      realtype_to_use=self.ctx_if_int_or(RealType('u8_rt'))
    )

  def evaluate_false(self, node):
    return self.evaluate_num(
      Node('num', value='0', pos=node.pos),
      realtype_to_use=self.ctx_if_int_or(RealType('u8_rt'))
    )

  def evaluate_continue_node_stmt(self, continue_node):
    if not self.inside_loop:
      error('use of `continue` statement outside of loop body', continue_node.pos)
    
    self.cur_builder.branch(self.loop[0])
  
  def evaluate_break_node_stmt(self, break_node):
    if not self.inside_loop:
      error('use of `break` statement outside of loop body', break_node.pos)
    
    self.cur_builder.branch(self.loop[1])

  def create_tmp_alloca_for_expraddr(self, realdata_expr):
    self.tmp_counter += 1

    tmp = self.allocas_builder.alloca(self.convert_realtype_to_llvmtype(realdata_expr.realtype), name=f'tmp.{self.tmp_counter}')
    self.llvm_store(self.cur_builder, realdata_expr.llvm_data, tmp)

    return tmp

  def evaluate_not_node(self, unary_node):
    realdata_expr = self.evaluate_node(unary_node.expr, self.ctx_if_int_or(RealType('u8_rt')))
    self.expect_realdata_is_integer(realdata_expr, unary_node.expr.pos)

    if realdata_expr.is_comptime_value():
      return RealData(
        realdata_expr.realtype,
        value=(new_value := not realdata_expr.value),
        llvm_data=ll.Constant(self.convert_realtype_to_llvmtype(realdata_expr.realtype), new_value),
      )

    return RealData(
      realdata_expr.realtype,
      llvm_data=self.llvm_not(
        self.cur_builder,
        realdata_expr.llvm_data,
        self.convert_realtype_to_llvmtype(realdata_expr.realtype)
      )
    )

  def evaluate_reference_node(self, unary_node):
    realdata_expr = self.evaluate_node(unary_node.expr, self.ctx.type if self.ctx.is_ptr() else REALTYPE_PLACEHOLDER)

    if isinstance(realdata_expr.llvm_data, ll.LoadInstr):
      self.cur_builder.remove(realdata_expr.llvm_data)
      realdata_expr.llvm_data = realdata_expr.llvm_data.operands[0]
    else:
      if unary_node.is_mut:
        error('temporary expression allocation address cannot be mutable', unary_node.pos)

      realdata_expr.llvm_data = self.create_tmp_alloca_for_expraddr(realdata_expr)
    
    realdata_expr.realtype = RealType('ptr_rt', is_mut=unary_node.is_mut, type=realdata_expr.realtype)

    return realdata_expr

  def evaluate_assignment_leftexpr_node(self, expr_node, assign_tok_pos):
    is_deref = self.is_deref_node(expr_node)
    is_index = self.is_index_node(expr_node)

    if is_index:
      old_expr_node = expr_node
      expr_node = expr_node.instance_expr
    elif is_deref:
      expr_node = expr_node.expr

    realdata_expr = self.evaluate_node(expr_node, REALTYPE_PLACEHOLDER)

    if is_index:
      if realdata_expr.realtype.is_ptr() and not realdata_expr.realtype.is_mut:
        error('cannot write at specific index to unmutable pointer', assign_tok_pos)
      
      expr_node = old_expr_node
      realdata_expr = self.internal_evaluate_index_node(expr_node, realdata_expr)
    
    if not isinstance(realdata_expr.llvm_data, ll.LoadInstr):
      error('cannot assign a value to an expression', expr_node.pos)
    
    if not is_deref:
      self.cur_builder.remove(realdata_expr.llvm_data)
      realdata_expr.llvm_data = realdata_expr.llvm_data.operands[0]
      realdata_expr.realtype = RealType('ptr_rt', is_mut=True, type=realdata_expr.realtype)

    return realdata_expr
  
  def evaluate_for_node_stmt(self, for_node):
    llvm_block_mid = self.cur_fn[1].append_basic_block('condcheck_block')
    llvm_block_loop = self.cur_fn[1].append_basic_block('loop_branch_block')
    llvm_block_right = self.cur_fn[1].append_basic_block('inc_branch_block')
    llvm_block_exit = self.cur_fn[1].append_basic_block('exit_branch_block')
    
    self.push_sub_scope()

    if for_node.left_node is not None:
      self.evaluate_var_decl_node_stmt(for_node.left_node)

    self.cur_builder.branch(llvm_block_mid)
    self.cur_builder = ll.IRBuilder(llvm_block_mid)

    cond_rd = self.evaluate_node(for_node.mid_node, RealType('u8_rt'))
    self.expect_realdata_is_integer(cond_rd, for_node.mid_node.pos)

    if cond_rd.is_comptime_value():
      self.cur_builder.branch(llvm_block_loop if cond_rd.value else llvm_block_exit)
    else:
      self.llvm_cbranch(self.cur_builder, cond_rd.llvm_data, llvm_block_loop, llvm_block_exit)

    self.push_builder(ll.IRBuilder(llvm_block_loop))
    self.push_loop((llvm_block_right, llvm_block_exit))

    has_terminator = self.evaluate_block(for_node.body)
    self.fix_sub_scope_terminator(has_terminator, llvm_block_right)

    self.push_builder(ll.IRBuilder(llvm_block_right))

    if for_node.right_node is not None:
      self.evaluate_stmt(for_node.right_node)

    self.cur_builder.branch(llvm_block_mid)
    self.pop_builder()

    self.pop_loop()
    self.pop_builder()
    self.pop_scope()

    self.cur_builder = ll.IRBuilder(llvm_block_exit)

  def evaluate_assignment_node_stmt(self, assignment_node):
    is_discard = assignment_node.lexpr.kind == '..'

    realdata_lexpr = self.evaluate_assignment_leftexpr_node(assignment_node.lexpr, assignment_node.pos) if not is_discard else None
    realdata_rexpr = self.evaluate_node(assignment_node.rexpr, realdata_lexpr.realtype.type if not is_discard else REALTYPE_PLACEHOLDER)

    if not is_discard:
      self.expect_realtype(realdata_lexpr.realtype.type, realdata_rexpr.realtype, assignment_node.pos)

    if not is_discard and not realdata_lexpr.realtype.is_mut:
      error('cannot write to unmutable pointer', assignment_node.pos)

    if is_discard and assignment_node.op.kind != '=':
      error('discard statement only accepts `=` as operator', assignment_node.pos)

    llvm_rexpr = {
      '=': lambda: realdata_rexpr.llvm_data,
      '+=': lambda: self.cur_builder.add(self.llvm_load(self.cur_builder, realdata_lexpr.llvm_data, self.convert_realtype_to_llvmtype(realdata_rexpr.realtype)), realdata_rexpr.llvm_data),
      '-=': lambda: self.cur_builder.sub(self.llvm_load(self.cur_builder, realdata_lexpr.llvm_data, self.convert_realtype_to_llvmtype(realdata_rexpr.realtype)), realdata_rexpr.llvm_data),
      '*=': lambda: self.cur_builder.mul(self.llvm_load(self.cur_builder, realdata_lexpr.llvm_data, self.convert_realtype_to_llvmtype(realdata_rexpr.realtype)), realdata_rexpr.llvm_data),
    }[assignment_node.op.kind]()

    if not is_discard:
      self.llvm_store(self.cur_builder, llvm_rexpr, realdata_lexpr.llvm_data)

  def evaluate_stmt(self, stmt):
    try:
      attr = getattr(self, f'evaluate_{stmt.kind}_stmt')
    except AttributeError:
      realdata = self.evaluate_node(stmt, REALTYPE_PLACEHOLDER, is_stmt=True)

      if not realdata.realtype.is_void():
        error(f'undiscarded expression of type `{realdata.realtype}` as statement', stmt.pos)
      
      return
    
    attr(stmt)
  
  def evaluate_chr(self, tok):
    return self.evaluate_num(
      Node(
        'num',
        value=str(ord(tok.value)),
        pos=tok.pos
      ),
      realtype_to_use=self.ctx_if_int_or(RealType('u8_rt'))
    )

  def evaluate_str(self, tok):
    if tok.value not in self.strings:
      self.str_counter += 1
      llvm_data = self.strings[tok.value] = ll.GlobalVariable(
        self.output,
        t := ll.ArrayType(ll.IntType(8), len(tok.value)),
        f'str.{self.str_counter}'
      )

      llvm_data.initializer = ll.Constant(t, bytearray(tok.value.encode('ascii')))
    else:
      llvm_data = self.strings[tok.value]

    if self.ctx == CSTRING_REALTYPE:
      return RealData(
        CSTRING_REALTYPE,
        llvm_data=llvm_data
      )
    
    agg = ll.Constant(self.convert_realtype_to_llvmtype(STRING_REALTYPE), ll.Undefined)
    llvm_data = self.llvm_insert_value(self.cur_builder, agg, llvm_data, 0, self.convert_realtype_to_llvmtype(CSTRING_REALTYPE))
    llvm_data = self.llvm_insert_value(self.cur_builder, llvm_data, ll.Constant(ll.IntType(64), len(tok.value)), 1, ll.IntType(64))

    return RealData(
      STRING_REALTYPE,
      llvm_data=llvm_data
    )

  def evaluate_block(self, block):
    for stmt in block:
      if self.cur_builder.block.is_terminated:
        error('unreachable code', stmt.pos)

      self.evaluate_stmt(stmt)
    
    return self.cur_builder.block.is_terminated

  def convert_realtype_to_llvmtype(self, realtype, in_progress_struct_rd_ids=[]):
    if realtype.is_int():
      return ll.IntType(realtype.bits)
    
    if realtype.is_float():
      return {
        16: ll.HalfType(),
        32: ll.FloatType(),
        64: ll.DoubleType(),
      }[realtype.bits]
    
    match realtype.kind:
      case 'ptr_rt':
        return ll.PointerType(self.convert_realtype_to_llvmtype(realtype.type, in_progress_struct_rd_ids))
      
      case 'void_rt':
        return ll.VoidType()
      
      case 'static_array_rt':
        return ll.ArrayType(self.convert_realtype_to_llvmtype(realtype.type, in_progress_struct_rd_ids), realtype.length)

      case 'struct_rt':
        if id(realtype) in in_progress_struct_rd_ids:
          return ll.IntType(1)

        return ll.LiteralStructType(list(map(
          lambda field: self.convert_realtype_to_llvmtype(field[1], in_progress_struct_rd_ids + [id(realtype)]),
          realtype.fields.items()
        )))

      case 'placeholder_rt':
        return ll.IntType(2)

      case _:
        raise NotImplementedError()

  def convert_proto_to_llvmproto(self, proto):
    match proto.kind:
      case 'fn_proto':
        return ll.FunctionType(
          self.convert_realtype_to_llvmtype(proto.ret_type),
          [self.convert_realtype_to_llvmtype(arg_type) for arg_type in proto.arg_types]
        )

      case _:
        raise NotImplementedError()

  def create_llvm_function(self, fn_name, proto):
    llvm_fn = ll.Function(
      self.output,
      self.convert_proto_to_llvmproto(proto),
      fn_name
    )

    llvm_fn.linkage = 'private' if fn_name != 'main' else 'external'

    return llvm_fn

  def push_ctx(self, realtype):
    self.ctx_types.append(realtype)

  def pop_ctx(self):
    self.ctx_types.pop()

  def push_builder(self, llvmbuilder):
    self.llvm_builders.append(llvmbuilder)

  def pop_builder(self):
    self.llvm_builders.pop()
  
  def llvm_load(self, builder, ptr, resulting_llvm_type):
    ptr = builder.bitcast(ptr, ll.PointerType(resulting_llvm_type))
    return builder.load(ptr)
  
  def llvm_gep(self, builder, ptr, indices, inbounds, real_expected_value_llvm_type=None):
    if real_expected_value_llvm_type is not None:
      real_expected_value_llvm_type = ll.PointerType(real_expected_value_llvm_type)
      ptr = builder.bitcast(ptr, real_expected_value_llvm_type)

    return builder.gep(ptr, indices, inbounds=inbounds)
  
  def llvm_insert_value(self, builder, agg, value, idx, real_expected_value_llvm_type):
    if isinstance(value.type, ll.PointerType):
      value = builder.bitcast(value, real_expected_value_llvm_type)

    return builder.insert_value(agg, value, idx)
  
  def llvm_extract_value(self, builder, agg, idx, resulting_llvm_type):
    r = builder.extract_value(agg, idx)

    if isinstance(resulting_llvm_type, ll.PointerType):
      r = builder.bitcast(r, resulting_llvm_type)
    
    return r
  
  def llvm_ret(self, builder, value, real_expected_value_llvm_type):
    if isinstance(value.type, ll.PointerType):
      value = builder.bitcast(value, real_expected_value_llvm_type)

    return builder.ret(value)
  
  def llvm_cbranch(self, builder, cond, truebr, falsebr):
    return builder.cbranch(
      builder.trunc(cond, ll.IntType(1)),
      truebr,
      falsebr
    )
  
  def llvm_icmp(self, builder, is_signed, op, value1, value2, llvm_type_the_result_should_be):
    llvm_fn = builder.icmp_signed if is_signed else builder.icmp_unsigned
    return builder.zext(
      llvm_fn(op, value1, value2),
      llvm_type_the_result_should_be
    )
  
  def llvm_fcmp(self, builder, op, value1, value2, llvm_type_the_result_should_be):
    return builder.zext(
      builder.fcmp_ordered(op, value1, value2),
      llvm_type_the_result_should_be
    )

  def llvm_phi(self, builder, incomings, llvm_type_the_resulting_should_be):
    p = builder.phi(llvm_type_the_resulting_should_be)

    for incoming in incomings:
      p.add_incoming(*incoming)

    return p

  def llvm_not(self, builder, value, llvm_type_the_resulting_should_be):
    return builder.zext(
      builder.icmp_signed('==', value, ll.Constant(llvm_type_the_resulting_should_be, 0)),
      llvm_type_the_resulting_should_be
    )

  def llvm_store(self, builder, value, ptr):
    ptr = builder.bitcast(ptr, ll.PointerType(value.type))
    return builder.store(value, ptr)
  
  def llvm_call(self, builder, fn, args):
    for i, arg in enumerate(args):
      if isinstance(arg.type, ll.PointerType):
        args[i] = builder.bitcast(arg, fn.ftype.args[i])

    return builder.call(fn, args)

  def declare_parameters(self, proto, fn_args):
    for i, (arg_name, arg_realtype) in enumerate(zip(map(lambda a: a.name, fn_args), proto.arg_types)):
      llvm_alloca = self.allocas_builder.alloca(typ := self.convert_realtype_to_llvmtype(arg_realtype), name=f'arg.{i + 1}')

      self.llvm_store(self.cur_builder, self.cur_fn[1].args[i], llvm_alloca)

      sym = Symbol(
        'local_var_sym',
        realtype=arg_realtype,
        llvm_alloca=llvm_alloca
      )

      self.map.declare_symbol(arg_name.value, sym, arg_name.pos)

  def gen_nongeneric_fn(self, fn):
    key = id(fn)

    if key in self.fn_in_evaluation:
      return self.fn_in_evaluation[key]
    
    if key in self.fn_evaluated:
      return self.fn_evaluated[key]

    self.push_scope()

    t = self.gen_fn(fn, fn.node.name.value, key)

    self.pop_scope()

    return t
  
  def declare_generics(self, generic_params, rt_generic_args):
    for param, rt_arg in zip(generic_params, rt_generic_args):
      self.map.declare_symbol(
        param.value,
        Symbol('alias_sym', realtype=rt_arg),
        param.pos
      )

  def gen_generic_fn(self, fn, rt_generics):
    key = (id(fn), rt_generics)

    if key in self.fn_in_evaluation:
      return self.fn_in_evaluation[key]
    
    if key in self.fn_evaluated:
      return self.fn_evaluated[key]
    
    # pushing the scope for the generics
    self.push_scope()
    self.declare_generics(fn.node.generics, rt_generics)

    fn_name = f'generic.{fn.node.name.value}<{", ".join(map(repr, rt_generics))}>'
    r = self.gen_fn(fn, fn_name, key)

    # popping the scope for the generics
    self.pop_scope()

    return r

  def gen_fn(self, fn, fn_name, key):
    proto = self.evaluate_fn_proto(fn.node)
    llvmfn = self.create_llvm_function(fn_name, proto)
  
    llvmfn_allocas_bb = llvmfn.append_basic_block('allocas')
    llvmfn_entry_bb = llvmfn.append_basic_block('entry')
  
    llvmbuilder_allocas = ll.IRBuilder(llvmfn_allocas_bb)
    llvmbuilder_entry = ll.IRBuilder(llvmfn_entry_bb)

    r = self.fn_in_evaluation[key] = (
      proto,
      llvmfn,
      llvmbuilder_allocas
    )

    old_loops = self.loops
    self.loops = []

    self.push_sub_scope()
    self.push_builder(llvmbuilder_entry)

    self.declare_parameters(proto, fn.node.args)
    has_terminator = self.evaluate_block(fn.node.body)
    llvmbuilder_allocas.branch(llvmfn_entry_bb)
    self.remove_dead_blocks()
    self.fix_ret_terminator(has_terminator, fn.node.pos)

    self.pop_builder()
    self.pop_scope()

    self.loops = old_loops
  
    self.fn_in_evaluation.remove_by_key(key)
    self.fn_evaluated[key] = r

    return r
  
  def remove_dead_blocks(self):
    alive_blocks = []

    for block in self.cur_fn[1].blocks:
      if not block.is_dead():
        alive_blocks.append(block)
    
    self.cur_fn[1].blocks = alive_blocks
  
  def fix_ret_terminator(self, has_terminator, fn_pos):
    if has_terminator:
      return
    
    if self.cur_fn[0].ret_type.is_void():
      self.cur_builder.ret_void()
      return

    if self.cur_builder.block.is_dead():
      return

    error('not all paths return a value', fn_pos)
  
  def push_sub_scope(self):
    self.maps.append(self.maps[-1].copy())
  
  def push_scope(self):
    self.maps.append(self.maps[0].copy())
  
  def pop_scope(self):
    self.maps.pop()

def get_main(mapped_ast):
  return mapped_ast.get_symbol('main', None)

def check_sym_is_fn(sym_id, sym):
  if sym.kind == 'fn_sym':
    return

  error(f'`{sym_id}` is not a function', sym.node.pos)

def check_sym_is_type(sym_id, sym):
  if sym.kind == 'type_sym':
    return

  error(f'`{sym_id}` is not a type', sym.node.pos)

def check_main_proto(main_proto, main_pos):
  throw_error = lambda: error('invalid `main` prototype', main_pos)

  if len(main_proto.arg_types) != 2:
    throw_error()
  
  if main_proto.arg_types != [
    RealType('u32_rt'),
    RealType('ptr_rt', is_mut=False, type=CSTRING_REALTYPE)
  ]:
    throw_error()

  if main_proto.ret_type.kind != 'i32_rt':
    throw_error()

def gen(mapped_ast):
  g = Generator(mapped_ast)
  main = get_main(mapped_ast)

  check_sym_is_fn('main', main)
  main_proto, _, _ = g.gen_nongeneric_fn(main)
  check_main_proto(main_proto, main.node.pos)

  return g.output