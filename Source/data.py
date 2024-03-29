from utils import equal_dicts, error, has_infinite_recursive_layout

indent_fmt = '  '

def repr_block(block):
  if block is None:
    return ''

  global indent_fmt
  
  indent_fmt += '  '
  t = f'\n\n{indent_fmt}'.join(map(repr, block))
  indent_fmt = indent_fmt[:-2]

  return f':\n{indent_fmt}  {t}'

class Node:
  def __init__(self, kind, **kwargs):
    self.__dict__ = kwargs
    self.kind = kind
  
  def __repr__(self):
    match self.kind:
      case 'fn_node':
        return f'fn {self.name}{self.args} -> {self.ret_type}{repr_block(self.body)}'
      
      case 'fn_arg_node':
        return f'{self.name}: {self.type}'
      
      case 'if_node':
        elif_branches = \
          f'\n{indent_fmt}' + (f'\n{indent_fmt}'.join(map(repr, self.elif_branches))) \
            if len(self.elif_branches) > 0 else ''
          
        else_branch = \
          f'\n{indent_fmt}{self.else_branch}' \
            if self.else_branch is not None else ''

        return f'{self.if_branch}{elif_branches}{else_branch}'
      
      case 'if_branch_node':
        return f'if {self.cond}{repr_block(self.body)}'

      case 'elif_branch_node':
        return f'elif {self.cond}{repr_block(self.body)}'

      case 'else_branch_node':
        return f'else{repr_block(self.body)}'

      case 'pass_node':
        return 'pass'

      case 'return_node':
        return f'return{f" {self.expr}" if self.expr is not None else ""}'

      case 'bin_node':
        return f'{self.left} {self.op.kind} {self.right}'
      
      case 'unary_node':
        match self.op.kind:
          case 'ref':
            return f'{self.expr}.ref'

          case '*':
            return f'{self.expr}.*'

          case _:
            return f'{self.op.kind}{self.expr}'

      case 'ptr_type_node':
        return f'*{self.type}'

      case 'type_decl_node':
        return f'type {self.name}{self.generics} = {self.type}'

      case 'call_node':
        return f'{self.name.value}{"!" if self.is_internal_call else ""}({", ".join(map(repr, self.args))})'
      
      case 'var_decl_node':
        return f'<var `{self.name.value}`: {self.type} = {self.expr}>'
      
      case 'inline_if_node':
        return f'({self.if_expr} if {self.if_cond} else {self.else_expr})'
      
      case 'struct_type_node':
        return f'({", ".join(self.fields)})'
      
      case 'struct_init_node':
        return f'<struct_init_node {self.fields}>'
      
      case 'struct_field_init_node':
        return f'{self.name.value}: {self.expr}'

      case 'try_node':
        return f'try {self.var if self.var is not None else ""}{self.expr}{repr_block(self.body)}'

      case 'var_try_node':
        return f'try {self.name.value}: {self.type} = '

      case 'out_param_node':
        return f'out {self.name.value}: {self.type}'
      
      case 'struct_field_node':
        return f'{self.name.value}: {self.type}'
      
      case 'as_node':
        return f'{self.expr}.cast({self.type})'
      
      case 'while_node':
        return f'while {self.cond}{repr_block(self.body)}'
      
      case 'for_node':
        return f'for {self.left_node}, {self.mid_node}, {self.right_node}{repr_block(self.body)}'
      
      case 'break_node':
        return 'break'
      
      case 'continue_node':
        return 'continue'
      
      case 'import_node':
        return f'from {self.path} import {self.ids}'
      
      case 'id_import_node':
        return f'{self.name} as {self.alias}'
      
      case 'index_node':
        return f'({self.instance_expr})[{self.index_expr}]'
      
      case 'generic_type_node':
        return f'{self.name.value}{self.generics}'
      
      case 'assignment_node':
        return f'{self.lexpr} {self.op} {self.rexpr}'
      
      case 'dot_node':
        return f'{self.left_expr}.{self.right_expr}'
      
      case 'array_type_node':
        return f'[{self.length} x {self.type}]'
      
      case 'array_init_node':
        return f'{self.nodes}'
      
      case 'union_type_node':
        return f'{self.fields}'

      case 'union_field_init_node':
        return f'{self.name}: {self.expr}'

      case 'union_field_node':
        return f'{self.name}: {self.type}'
      
      case 'test_node':
        return f'test {self.desc}{repr_block(self.body)}'

      case 'chr':
        s = self.value.replace("`", "\\`")
        return f"`{s}`"

      case 'str':
        s = self.value.replace("'", "\\'")
        return f"'{s}'"
      
      case 'fn_type_node':
        return f'fn({", ".join(self.arg_types)}) -> {self.ret_type}'
      
      case 'enum_node':
        return f'.{self.id}'

      case _:
        try:
          return str(self.value)
        except AttributeError:
          raise NotImplementedError(self.kind)

class MappedAst:
  def __init__(self):
    self.symbols = {}
  
  def copy(self):
    m = MappedAst()
    m.symbols = self.symbols.copy()

    return m
  
  def declare_symbol(self, id, sym, pos):
    from gen import BUILTINS_TABLE

    if id in BUILTINS_TABLE:
      error(f'id `{id}` is reserved')

    if id in self.symbols:
      error(f'id `{id}` already declared', pos)
    
    self.symbols[id] = sym
  
  def is_declared(self, id):
    return id in self.symbols
  
  def get_symbol(self, id, pos):
    if id not in self.symbols:
      error(f'id `{id}` not declared', pos)

    return self.symbols[id]
  
  def __repr__(self):
    r = ''

    for id, sym in self.symbols.items():
      r += f'+ Symbol `{id}` -> {sym}\n\n'
    
    return r

class Symbol:
  def __init__(self, kind, **kwargs):
    assert kind.endswith('_sym')

    self.__dict__ = kwargs
    self.kind = kind
  
  def __repr__(self):
    return f'Symbol(`{self.kind}`)\n  {self.node}'

class Proto:
  def __init__(self, kind, **kwargs):
    assert kind.endswith('_proto')

    self.__dict__ = kwargs
    self.kind = kind
  
  def __repr__(self):
    return f'<repr Proto {self.__dict__}>'

class RealType:
  def __init__(self, kind, **kwargs):
    assert kind.endswith('_rt')

    self.__dict__ = kwargs
    self.kind = kind
  
  @property
  def bits(self):
    assert self.is_numeric()

    return int(self.kind[1:-3])
  
  @property
  def is_signed(self):
    assert self.is_numeric()

    return self.kind[0] == 'i'
  
  def can_be_handled_by_a_constant(self):
    return self.is_numeric() or self.is_ptr()
  
  def is_int(self):
    return self.kind in ['i8_rt', 'i16_rt', 'i32_rt', 'i64_rt', 'u8_rt', 'u16_rt', 'u32_rt', 'u64_rt']
  
  def is_fn(self):
    return self.kind == 'fn_rt'

  def is_void(self):
    return self.kind == 'void_rt'
  
  def is_static_array(self):
    return self.kind == 'static_array_rt'

  def could_be_fat_pointer(self):
    # todo: len should be a generic integer
    return \
      self.kind == 'struct_rt' and \
        list(self.fields.keys()) == ['ptr', 'len'] and \
          self.fields['ptr'].is_ptr() and \
            self.fields['len'] == RealType('u64_rt')

  def is_float(self):
    return self.kind in ['f32_rt', 'f64_rt']

  def is_numeric(self):
    return self.is_int() or self.is_float()

  def is_union(self):
    return self.kind == 'union_rt'

  def is_ptr(self):
    return self.kind == 'ptr_rt'

  def is_mut_ptr(self):
    return self.is_ptr() and self.is_mut

  def is_const_ptr(self):
    return self.is_ptr() and not self.is_mut
  
  def is_struct(self):
    return self.kind == 'struct_rt'
  
  def calculate_size(self):
    if self.is_numeric():
      return self.bits // 8

    match self.kind:
      case 'ptr_rt':
        return 8 # todo: return the size of the target arch
      
      case 'struct_rt':
        return max(map(lambda k: self.fields[k].calculate_size(), self.fields)) * len(self.fields)

      case 'union_rt':
        return max(map(lambda k: self.fields[k].calculate_size(), self.fields))
      
      case 'static_array_rt' | 'static_vector_rt':
        return self.type.calculate_size() * self.length

      case _:
        raise NotImplementedError()
  
  def is_valid_realtype(self):
    return self.kind not in ['placeholder_rt', 'generic_to_infer_rt']
  
  def contains_generics_to_infer(self):
    match self.kind:
      case 'ptr_rt' | 'static_array_rt' | 'static_vector_rt':
        return self.type.contains_generics_to_infer()
      
      case 'fn_rt':
        return \
          any(arg_rt.contains_generics_to_infer() for arg_rt in self.arg_types) or \
          self.ret_type.contains_generics_to_infer()
      
      case 'union_rt' | 'struct_rt':
        return any(field_rt.contains_generics_to_infer() for field_rt in self.fields.values())

      case 'generic_to_infer_rt':
        return True

      case _:
        return False

  def internal_eq(self, obj, in_progress_struct_rt_ids=[]):
    if not isinstance(obj, RealType):
      return False
    
    if 'generic_to_infer_rt' in [self.kind, obj.kind]:
      return True
    
    key = (id(self), id(obj))
    specials = ['struct_rt', 'union_rt']
    
    if self.kind in specials or obj.kind in specials:
      if key in in_progress_struct_rt_ids:
        return True
    else:
      if self.kind != obj.kind:
        return False
      
      match self.kind:
        case 'ptr_rt':
          if 'generic_to_infer_rt' in [self.type.kind, obj.type.kind]:
            return True

          return self.is_mut == obj.is_mut and self.type.internal_eq(obj.type, in_progress_struct_rt_ids)
        
        case 'static_array_rt' | 'static_vector_rt':
          if 'generic_to_infer_rt' in [self.type.kind, obj.type.kind]:
            return True

          return self.length == obj.length and self.type.internal_eq(obj.type, in_progress_struct_rt_ids)

        case _:
          return equal_dicts(self.__dict__, obj.__dict__, ['aka'])
    
    if self.kind != obj.kind:
      return False

    if len(self.fields) != len(obj.fields):
      return False

    for (name1, rt1), (name2, rt2) in zip(self.fields.items(), obj.fields.items()):
      if name1 != name2 or not rt1.internal_eq(rt2, in_progress_struct_rt_ids + [key]):
        return False
    
    return True

  def __eq__(self, obj):
    return self.internal_eq(obj)

  def internal_repr(self, in_progress_struct_rt_ids=[]):
    if hasattr(self, 'aka'):
      return self.aka

    if self.is_numeric():
      return self.kind[:-3]
    
    match self.kind:
      case 'ptr_rt':
        return f'*{"mut " if self.is_mut else ""}{self.type.internal_repr(in_progress_struct_rt_ids)}'
      
      case 'struct_rt':
        if id(self) in in_progress_struct_rt_ids:
          return '(..)'

        fields = ", ".join(
          map(lambda field: f"{field[0]}: {field[1].internal_repr(in_progress_struct_rt_ids + [id(self)])}", self.fields.items())
        )

        return f'({fields})'

      case 'static_array_rt':
        return f'[{self.length} x {self.type.internal_repr(in_progress_struct_rt_ids)}]'
      
      case 'static_vector_rt':
        return f'<{self.length} x {self.type.internal_repr(in_progress_struct_rt_ids)}>'

      case 'void_rt':
        return 'void'
      
      case 'placeholder_rt':
        return '<placeholder_type>'
      
      case 'fn_rt':
        return f'fn({", ".join(map(lambda rt: rt.internal_repr(in_progress_struct_rt_ids), self.arg_types))}) -> {self.ret_type.internal_repr(in_progress_struct_rt_ids)}'
      
      case 'union_rt':
        fields = ", ".join(
          map(lambda field: f"{field[0]}: {field[1].internal_repr(in_progress_struct_rt_ids)}", self.fields.items())
        )

        return f'[{fields}]'

      case 'generic_to_infer_rt':
        return f'${self.id}'

      case _:
        raise NotImplementedError(self.kind)

  def __repr__(self):
    return self.internal_repr()

class RealData:
  def __init__(self, realtype, **kwargs):
    assert 'llvm_data' in kwargs

    self.__dict__ = kwargs
    self.realtype = realtype
  
  def is_comptime_value(self):
    return hasattr(self, 'value')
  
  def is_true(self):
    assert self.is_comptime_value()

    return self.value == 1
  
  def realtype_is_coercable(self):
    return self.is_comptime_value() and not hasattr(self, 'realtype_is_coerced')
  
  def has_float_value(self):
    return isinstance(self.value, float)
  
  def has_int_value(self):
    return isinstance(self.value, int)
  
  def has_numeric_value(self):
    return self.has_int_value() or self.has_float_value()
  
  def __repr__(self):
    return f'<repr RealData {self.__dict__}>'

class ComparatorDict:
  def __init__(self):
    self.items = []
  
  def values(self):
    return [v for _, v in self.items]

  def keys(self):
    return [k for k, _ in self.items]

  def __setitem__(self, key, value):
    for i, (k, _) in enumerate(self.items):
      if k == key:
        self.items[i] = (key, value)
        return

    self.items.append((key, value))

  def __getitem__(self, key):
    for k, v in self.items:
      if k == key:
        return v

    raise KeyError
  
  def __contains__(self, key):
    for k, _ in self.items:
      if k == key:
        return True

    return False
  
  def __len__(self):
    return len(self.items)

  def remove_by_key(self, key):
    for i, (k, _) in enumerate(self.items):
      if k == key:
        self.items.pop(i)
        return

    raise KeyError
