from utils import error, has_infinite_recursive_layout

RESERVED_SYMBOLS = [
  'i8', 'i16', 'i32', 'i64',
  'u8', 'u16', 'u32', 'u64',
  'void'
]

indent_fmt = '  '

def repr_block(block):
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
        return f'({self.left} {self.op.kind} {self.right})'
      
      case 'unary_node':
        return f'({self.op.kind} {self.expr})'

      case 'ptr_type_node':
        return f'*{self.type}'

      case 'type_decl_node':
        return f'type {self.name} = {self.type}'

      case 'call_ptr_node':
        return f'<call_ptr {self.expr}, {self.args}>'

      case 'call_node':
        return f'<call `{self.name.value}`, {self.args}>'
      
      case 'var_decl_node':
        return f'<var `{self.name.value}`: {self.type} = {self.expr}>'
      
      case 'struct_type_node':
        return f'<struct_type_node {self.fields}>'
      
      case 'struct_init_node':
        return f'<struct_init_node {self.fields}>'
      
      case 'struct_field_init_node':
        return f'{self.name.value}: {self.expr}'
      
      case 'struct_field_node':
        return f'{self.name.value}: {self.type}'
      
      case 'as_node':
        return f'<as_node from {self.expr}, to {self.type}>'
      
      case 'while_node':
        return f'while {self.cond}{repr_block(self.body)}'
      
      case 'for_node':
        return f'for {self.left_node}, {self.mid_node}, {self.right_node}{repr_block(self.body)}'
      
      case 'break_node':
        return 'break'
      
      case 'continue_node':
        return 'continue'
      
      case 'assignment_node':
        return f'{self.lexpr} {self.op} {self.rexpr}'
      
      case 'dot_node':
        return f'{self.left_expr}.{self.right_expr}'

      case _:
        return f'<repr `{self.value}`>'

class MappedAst:
  def __init__(self):
    self.symbols = {}
  
  def copy(self):
    m = MappedAst()
    m.symbols = self.symbols.copy()

    return m
  
  def declare_symbol(self, id, sym, pos):
    if id in RESERVED_SYMBOLS:
      error(f'symbol `{id}` is reserved')

    if id in self.symbols:
      error(f'symbol `{id}` already declared', pos)
    
    self.symbols[id] = sym
  
  def is_declared(self, id):
    return id in self.symbols
  
  def get_symbol(self, id, pos):
    if id not in self.symbols:
      error(f'symbol `{id}` not declared', pos)

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
  
  def is_int(self):
    return self.kind in ['i8_rt', 'i16_rt', 'i32_rt', 'i64_rt', 'u8_rt', 'u16_rt', 'u32_rt', 'u64_rt']
  
  def is_void(self):
    return self.kind == 'void_rt'

  def is_numeric(self):
    return self.is_int()

  def is_ptr(self):
    return self.kind == 'ptr_rt'
  
  def is_struct(self):
    return self.kind == 'struct_rt'
  
  def internal_eq(self, obj, in_progress_struct_rt_ids=[]):
    if not isinstance(obj, RealType):
      return False
    
    key = (id(self), id(obj))
    
    if self.is_struct() and obj.is_struct():
      if key in in_progress_struct_rt_ids:
        return True
    else:
      if self.kind != obj.kind:
        return False
      
      match self.kind:
        case 'ptr_rt':
          return self.type.internal_eq(obj.type, in_progress_struct_rt_ids)

        case _:
          return self.__dict__ == obj.__dict__
      
    for (name1, rt1), (name2, rt2) in zip(self.fields.items(), obj.fields.items()):
      if name1 != name2 or not rt1.internal_eq(rt2, in_progress_struct_rt_ids + [key]):
        return False
    
    return True

  def __eq__(self, obj):
    return self.internal_eq(obj)

  def internal_repr(self, in_progress_struct_rt_ids=[]):
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

      case 'void_rt':
        return 'void'
      
      case 'placeholder_rt':
        return '<placeholder_type>'

      case _:
        raise NotImplementedError()

  def __repr__(self):
    return self.internal_repr()

class RealData:
  def __init__(self, realtype, **kwargs):
    assert 'llvm_data' in kwargs

    self.__dict__ = kwargs
    self.realtype = realtype
  
  def is_comptime_value(self):
    return hasattr(self, 'value')
  
  def realtype_is_coercable(self):
    return self.is_comptime_value() and not hasattr(self, 'realtype_is_coerced')
  
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