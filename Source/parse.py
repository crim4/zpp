from data import Node
from utils import error, fix_package_path

UNALLOWED_ON_BLOCK_DEFER_NODE = [
  'return_node', 'break_node',
  'continue_node', 'defer_node'
]

UNALLOWED_ON_INLINE_DEFER_NODE = [
  'pass_node', 'if_node', 'while_node',
  'for_node', 'var_decl_node'
] + UNALLOWED_ON_BLOCK_DEFER_NODE

class Parser:
  def __init__(self, toks):
    self.toks = toks
    self.index = 0
    self.indents = [0]
    self.stmt_parser_fn = self.parse_stmt
  
  @property
  def eof_pos(self):
    return self.toks[-1].pos if len(self.toks) > 0 else (1, 1)

  @property
  def cur(self):
    return self.toks[self.index] if self.has_tok else error('unexpected `eof`', self.eof_pos)

  @property
  def has_tok(self):
    return self.index < len(self.toks)

  @property
  def cur_indent(self):
    return self.indents[-1]

  def make_node(self, kind, **kwargs):
    assert 'pos' in kwargs
    assert kind.endswith('_node')

    return Node(kind, **kwargs)

  def advance(self, count=1):
    self.index += count

  def consume_cur(self):
    t = self.cur
    self.advance()

    return t
  
  def match_tok(self, kind, allow_on_new_line=False):
    if not self.has_tok:
      return False

    return self.cur.kind == kind and (allow_on_new_line or not self.cur.is_on_new_line)

  def expect_and_consume(self, kind, allow_on_new_line=False):
    if self.cur.kind != kind:
      error(f'expected `{kind}`, found `{self.cur.kind}`', self.cur.pos)
    
    if not allow_on_new_line and self.cur.is_on_new_line:
      error(f'unexpected token to be on a new line', self.cur.pos)
    
    return self.consume_cur()

  def parse_struct_fields(self, is_union_node=False):
    fields = []
    field_nodekind = 'struct_field_node' if not is_union_node else 'union_field_node'

    while True:
      name = self.expect_and_consume('id', allow_on_new_line=True)
      self.expect_and_consume(':')
      type = self.parse_type()

      fields.append(self.make_node(field_nodekind, name=name, type=type, pos=name.pos))

      if not self.match_tok(','):
        break
      
      self.advance()

    self.expect_and_consume(')' if not is_union_node else ']', allow_on_new_line=True)
    return fields
  
  def parse_union_type(self, pos):
    fields = self.parse_struct_fields(is_union_node=True)

    return self.make_node(
      'union_type_node',
      fields=fields,
      pos=pos
    )

  def parse_type(self):
    if self.match_tok('fn'):
      pos = self.consume_cur().pos
      arg_types, _ = self.parse_fn_args(parse_only_types=True)
      ret_type = self.parse_fn_ret_type()

      return self.make_node(
        'fn_type_node',
        arg_types=arg_types,
        ret_type=ret_type,
        pos=pos
      )
  
    if self.match_tok('*'):
      pos = self.consume_cur().pos
      is_mut = self.consume_tok_if_match('mut') is not None
      return self.make_node('ptr_type_node', is_mut=is_mut, type=self.parse_type(), pos=pos)
    
    if self.match_toks(['[', '<']):
      tok = self.consume_cur()
      pos = tok.pos

      if self.match_pattern(['id', ':'], allow_first_on_new_line=True):
        return self.parse_union_type(pos)

      length = self.parse_expr()
      i = self.expect_and_consume('id')

      if i.value != 'x':
        error('expected token `x`', i.pos)
      
      type = self.parse_type()
      self.expect_and_consume(']' if tok.kind == '[' else '>')
      return self.make_node(
        'array_type_node' if tok.kind == '[' else 'vector_type_node',
        length=length,
        type=type,
        pos=pos
      )
    
    if self.match_tok('('):
      pos = self.consume_cur().pos
      fields = self.parse_struct_fields()
      
      return self.make_node(
        'struct_type_node',
        fields=fields,
        pos=pos
      )

    t = self.expect_and_consume('id')

    if self.match_tok('['):
      pos = self.cur.pos
      generics = self.parse_generics_in_call(use_fn_notation=False)

      return self.make_node(
        'generic_type_node',
        name=t,
        generics=generics,
        pos=pos
      )
    
    return t

  def parse_generics(self, use_fn_notation=True):
    opening_tokkind = '|' if use_fn_notation else '['
    closing_tokkind = '|' if use_fn_notation else ']'

    if not self.match_tok(opening_tokkind):
      return []
    
    self.expect_and_consume(opening_tokkind)
    generics = []

    while True:
      t = self.expect_and_consume('id')
      generics.append(t)

      if not self.match_tok(','):
        break
      
      self.advance()
    
    self.expect_and_consume(closing_tokkind)
    return generics

  def parse_generics_in_call(self, use_fn_notation=True):
    opening_tokkind = '|' if use_fn_notation else '['
    closing_tokkind = '|' if use_fn_notation else ']'

    if not self.match_tok(opening_tokkind, allow_on_new_line=use_fn_notation):
      return []
    
    self.expect_and_consume(opening_tokkind, allow_on_new_line=use_fn_notation)
    generics = []

    while True:
      t = self.parse_type()
      generics.append(t)

      if not self.match_tok(','):
        break
      
      self.advance()
    
    self.expect_and_consume(closing_tokkind)
    return generics

  def parse_fn_args(self, parse_only_types=False):
    args = []
    self.expect_and_consume('(')

    if parse_only_types:
      generics = []
    else:
      generics = self.parse_generics()

    while True:
      if len(args) == 0 and self.match_tok(')', allow_on_new_line=True):
        break

      if parse_only_types:
        args.append(self.parse_type())
      else:
        name = self.expect_and_consume('id', allow_on_new_line=True)
        self.expect_and_consume(':')
        type = self.parse_type()

        args.append(self.make_node('fn_arg_node', name=name, type=type, pos=name.pos))

      if not self.match_tok(','):
        break
      
      self.advance()

    self.expect_and_consume(')', allow_on_new_line=True)
    return (args, generics)
  
  def parse_fn_ret_type(self):
    self.expect_and_consume('->')

    return self.parse_type()

  def parse_bin(self, allow_left_on_new_line, ops, terms_parser_fn):
    self.throw_error_when_tok_on_new_line_and_not_allowed(allow_left_on_new_line)

    left = terms_parser_fn()
    
    while self.match_toks(ops):
      op = self.consume_cur()
      right = terms_parser_fn()

      left = self.make_node(
        'bin_node',
        op=op,
        left=left,
        right=right,
        pos=op.pos
      )
    
    return left
  
  def parse_out_param(self):
    pos = self.consume_cur().pos
    name = self.expect_and_consume('id')
    self.expect_and_consume(':')
    type = self.parse_type()

    return self.make_node(
      'out_param_node',
      name=name,
      type=type,
      pos=pos
    )
  
  def parse_call_node(self, node_to_call, is_internal_call):
    args = []
    pos = self.expect_and_consume('(').pos
    generics = self.parse_generics_in_call()

    while True:
      if len(args) == 0 and self.match_tok(')', allow_on_new_line=True):
        break

      if self.match_tok('out', allow_on_new_line=True):
        args.append(self.parse_out_param())
      else:
        args.append(self.parse_expr(allow_left_on_new_line=True))

      if not self.match_tok(','):
        break
      
      self.advance()

    self.expect_and_consume(')', allow_on_new_line=True)

    if node_to_call.kind == 'dot_node':
      args.insert(0, node_to_call.left_expr)
      node_to_call = node_to_call.right_expr
    
    return self.make_node(
      'call_node',
      name=node_to_call,
      generics=generics,
      is_internal_call=is_internal_call,
      args=args,
      pos=pos
    )
  
  def match_pattern(self, pattern_toks, allow_first_on_new_line=False):
    old_index = self.index

    for i, tok in enumerate(pattern_toks):
      if tok is None:
        # assert its the last one
        assert i == len(pattern_toks) - 1

        cur_is_on_new_line = self.cur.is_on_new_line
        r = self.index - old_index
        self.index = old_index

        return 0 if cur_is_on_new_line else r + 1

      if not self.match_tok(tok, allow_on_new_line=allow_first_on_new_line and i == 0):
        self.index = old_index
        return 0
      
      self.advance()
    
    r = self.index - old_index
    self.index = old_index
    
    return r
  
  def parse_array_init_node(self, pos):
    nodes = []

    while True:
      nodes.append(self.parse_expr(allow_left_on_new_line=True))

      if not self.match_tok(','):
        break
      
      self.advance()

    self.expect_and_consume(']', allow_on_new_line=True)
    
    return self.make_node(
      'array_init_node',
      nodes=nodes,
      pos=pos
    )

  def parse_term(self):
    term = self.consume_cur()

    match term.kind:
      case 'num' | 'fnum' | 'id' | 'True' | 'False' | 'None' | 'Undefined' | 'Ok' | 'Err' | 'str' | 'chr':
        pass
      
      case '.':
        term = Node('enum_node', id=self.expect_and_consume('id'), pos=term.pos)
    
      case '[':
        if self.match_pattern(['id', ':'], allow_first_on_new_line=True):
          term = self.parse_struct_init_node(term.pos, is_union_node=True)
        else:
          term = self.parse_array_init_node(term.pos)
      
      case 'cast':
        term = self.parse_cast_node(None, term.pos)
    
      case '+' | '-' | 'not' | 'ref' | 'mut' | '*':
        op = term
        is_mut = op.kind == 'mut'
        expr = self.parse_term()

        term = self.make_node(
          'unary_node',
          op=op,
          expr=expr,
          is_mut=is_mut,
          pos=op.pos
        )
      
      case '(':
        if self.match_pattern(['id', ':'], True):
          term = self.parse_struct_init_node(term.pos)
        else:
          term = self.parse_expr(allow_left_on_new_line=True)
          self.expect_and_consume(')', allow_on_new_line=True)

      case _:
        error('invalid term in expression', term.pos)
    
    while self.has_tok and not self.cur.is_on_new_line and self.match_toks(['.', '[', '(', '!'], allow_on_new_line=True):
      if self.cur.kind == '[':
        pos = self.consume_cur().pos
        term = self.make_node('index_node', instance_expr=term, index_expr=self.parse_expr(), pos=pos)
        self.expect_and_consume(']')
        continue
      
      # matching call node
      if self.cur.kind in ['!', '(']:
        if term.kind not in ['id', 'dot_node']:
          error('expected id, to invoke pointers use `Invoke!()`', term.pos)

        is_internal_call = self.consume_tok_if_match('!') is not None
        term = self.parse_call_node(term, is_internal_call)
        continue

      dot_tok = self.consume_cur()
      left_expr = term

      if dot_tok.kind == '.' and self.match_toks(['mut', 'ref', '*', 'cast']):
        op = self.consume_cur()
        is_mut = op.kind == 'mut'

        if op.kind == 'cast':
          term = self.parse_cast_node(term, op.pos)
        else:
          term = self.make_node(
            'unary_node',
            op=op,
            expr=term,
            is_mut=is_mut,
            pos=dot_tok.pos
          )
        
        term.is_chained_form = True
        continue
      
      right_expr = self.expect_and_consume('id')

      term = self.make_node(
        'dot_node',
        left_expr=left_expr,
        right_expr=right_expr,
        pos=dot_tok.pos
      )
    
    if term.kind in ['unary_node', 'as_node'] and hasattr(term, 'is_chained_form') and term.is_chained_form:
      error(f'please use `{term.op.value} expr` instead, `expr.{term.op.value}` is reserved for chaining', term.pos)
    
    return term
  
  def parse_cast_node(self, term_node, pos):
    self.expect_and_consume('(')
    type_node = self.parse_type()
    self.expect_and_consume(')')

    if term_node is None:
      term_node = self.parse_term()

    return self.make_node(
      'as_node',
      op=Node('cast', value='cast', pos=pos),
      expr=term_node,
      type=type_node,
      pos=pos
    )
  
  def parse_struct_init_node(self, pos, is_union_node=False):
    fields = []
    field_nodekind = 'struct_field_init_node' if not is_union_node else 'union_field_init_node'

    while True:
      name = self.expect_and_consume('id', allow_on_new_line=True)
      self.expect_and_consume(':')
      expr = self.parse_expr()

      fields.append(self.make_node(field_nodekind, name=name, expr=expr, pos=name.pos))

      if not self.match_tok(','):
        break
      
      self.advance()

    self.expect_and_consume(')' if not is_union_node else ']', allow_on_new_line=True)

    if is_union_node and len(fields) > 1:
      error('union initializer can only contain one field assignment', pos)

    return self.make_node(
      'struct_init_node' if not is_union_node else 'union_init_node',
      fields=fields,
      pos=pos
    )
  
  def parse_inline_if_node(self, if_expr):
    pos = self.consume_cur().pos
    if_cond = self.parse_expr()
    self.expect_and_consume('else')
    else_expr = self.parse_expr()

    return self.make_node(
      'inline_if_node',
      if_expr=if_expr,
      if_cond=if_cond,
      else_expr=else_expr,
      pos=pos
    )

  def throw_error_when_tok_on_new_line_and_not_allowed(self, allow_left_on_new_line):
    if not allow_left_on_new_line and self.cur.is_on_new_line:
      error('expression not allowed to be on a new line', self.cur.pos)  

  def consume_tok_if_match(self, kind, allow_on_new_line=False):
    if not self.match_tok(kind, allow_on_new_line=allow_on_new_line):
      return

    return self.consume_cur()

  def parse_expr(self, allow_left_on_new_line=False):
    expr = self.parse_bin(
      allow_left_on_new_line, ['or'], lambda: self.parse_bin(
        allow_left_on_new_line,['and'], lambda: self.parse_bin(
          allow_left_on_new_line, ['==', '!=', '<', '>', '<=', '>='], lambda: self.parse_bin(
            allow_left_on_new_line, ['+', '-'], lambda: self.parse_bin(
              allow_left_on_new_line, ['*', '/', '%'], lambda: self.parse_term()
            )
          )
        )
      )
    )

    while self.match_toks(['if']):
      match self.cur.kind:
        case 'if':
          expr = self.parse_inline_if_node(expr)

        case _:
          raise NotImplementedError()
    
    return expr


  def match_toks(self, toks, allow_on_new_line=False):
    for tok in toks:
      if self.match_tok(tok, allow_on_new_line=allow_on_new_line):
        return True
    
    return False

  def parse_if_node(self):
    if_branch = None
    elif_branches = []
    else_branch = None

    make_if_node = lambda: self.make_node(
      'if_node',
      if_branch=if_branch,
      elif_branches=elif_branches,
      else_branch=else_branch,
      pos=if_branch.pos
    )

    while self.match_toks(['if', 'elif', 'else'], allow_on_new_line=True):
      if self.cur.indent > self.cur_indent:
        error('invalid indent', self.cur.pos)
      
      if self.cur.indent < self.cur_indent:
        break

      branch_kind = self.consume_cur()

      match branch_kind.kind:
        case 'if':
          # we matched a separated if statement
          if if_branch is not None:
            self.advance(-1)
            break
          
          cond = self.parse_expr()
          block = self.parse_block()
          
          if_branch = self.make_node(
            'if_branch_node',
            cond=cond,
            body=block,
            pos=branch_kind.pos
          )
        
        case 'elif':
          cond = self.parse_expr()
          block = self.parse_block()

          elif_branches.append(self.make_node(
            'elif_branch_node',
            cond=cond,
            body=block,
            pos=branch_kind.pos
          ))
        
        case 'else':
          block = self.parse_block()

          else_branch = self.make_node(
            'else_branch_node',
            body=block,
            pos=branch_kind.pos
          )

          break

        case _:
          raise NotImplementedError()
    
    return make_if_node()

  def parse_return_node(self):
    pos = self.consume_cur().pos
    expr = self.parse_expr() if self.has_tok and not self.cur.is_on_new_line else None

    return self.make_node(
      'return_node',
      expr=expr,
      pos=pos
    )
  
  def parse_case_node(self):
    if self.match_tok('else', allow_on_new_line=True):
      pos = self.consume_cur().pos
      body = self.parse_block()

      return self.make_node(
        'else_branch_node',
        body=body,
        pos=pos
      )

    pos = self.expect_and_consume('case', allow_on_new_line=True).pos
    expr = []

    while True:
      expr.append(self.parse_expr(allow_left_on_new_line=True))

      if not self.match_tok(','):
        break

      self.consume_cur()

    body = self.parse_block()

    return self.make_node(
      'case_branch_node',
      expr=expr,
      body=body,
      pos=pos
    )
  
  def collect_match_block(self):
    case_branches = []
    else_branch = None
    self.expect_and_consume(':')

    if not self.cur.is_on_new_line:
      error('match cases cannot be inlined', self.cur.pos)

    if self.cur.indent <= self.cur_indent:
      error('invalid indent', self.cur.pos)

    self.indents.append(self.cur.indent)

    while True:
      case_node = self.parse_case_node()

      if case_node.kind == 'else_branch_node':
        else_branch = case_node
        break

      case_branches.append(case_node)

      if not self.has_tok or self.cur.indent < self.cur_indent:
        break

      if self.cur.indent > self.cur_indent:
        error('invalid indent', self.cur.pos)
    
    self.indents.pop()
    return case_branches, else_branch

  def parse_match_node(self):
    pos = self.consume_cur().pos
    expr_to_match = self.parse_expr()
    case_branches, else_branch = self.collect_match_block()

    if len(case_branches) == 0:
      error('match must have at least one case branch', pos)

    return self.make_node(
      'match_node',
      expr_to_match=expr_to_match,
      case_branches=case_branches,
      else_branch=else_branch,
      pos=pos
    )
  
  def parse_inline_stmt(self):
    node = self.parse_stmt()

    if node.kind in UNALLOWED_ON_INLINE_DEFER_NODE:
      error('this statement is not allowed here', node.pos)
    
    return node

  def parse_stmt_without_control_flow(self):
    node = self.parse_stmt()

    if node.kind in UNALLOWED_ON_BLOCK_DEFER_NODE:
      error('this statement is not allowed here', node.pos)
    
    return node

  def parse_defer_node(self):
    pos = self.consume_cur().pos

    if self.match_tok(':'):
      old, self.stmt_parser_fn = self.stmt_parser_fn, self.parse_stmt_without_control_flow
      body = self.parse_block()
      self.stmt_parser_fn = old
    else:
      body = [self.parse_inline_stmt()]

    return self.make_node(
      'defer_node',
      body=body,
      pos=pos
    )

  def parse_try_node(self):
    pos = self.consume_cur().pos
    var = None

    # None stands for any token assuming it's on the same line
    if self.match_pattern(['id', ':', None]):
      name = self.expect_and_consume('id')
      self.expect_and_consume(':')
      type = self.parse_type()
      self.expect_and_consume('=')

      var = self.make_node(
        'var_try_node',
        name=name,
        type=type,
        pos=name.pos
      )

    expr = self.parse_expr()
    body = self.parse_block() if self.match_tok(':') else None

    if var is not None and body is None:
      error('var is not allowed when the try statement has no block', pos)

    return self.make_node(
      'try_node',
      var=var,
      expr=expr,
      body=body,
      pos=pos
    )

  def parse_var_decl(self):
    name = self.expect_and_consume('id', allow_on_new_line=True)
    self.expect_and_consume(':')

    type = self.parse_type()
    self.expect_and_consume('=')

    expr = self.parse_expr()

    return self.make_node(
      'var_decl_node',
      name=name,
      type=type,
      expr=expr,
      pos=name.pos
    )

  def parse_while_node(self):
    pos = self.consume_cur().pos

    cond = self.parse_expr()
    body = self.parse_block()

    return self.make_node(
      'while_node',
      cond=cond,
      body=body,
      pos=pos
    )

  def parse_for_node(self):
    pos = self.consume_cur().pos
    left_node = self.parse_var_decl() if self.consume_tok_if_match('..') is None else None
    self.expect_and_consume(',')
    mid_node = self.parse_expr()
    self.expect_and_consume(',')
    
    if self.match_pattern(['..', ':']):
      self.advance()
      right_node = None
    else:
      right_node = self.parse_inline_stmt()
      
    body = self.parse_block()

    return self.make_node(
      'for_node',
      left_node=left_node,
      mid_node=mid_node,
      right_node=right_node,
      body=body,
      pos=pos
    )

  def parse_stmt(self):
    match self.cur.kind:
      case 'pass':
        return self.make_node('pass_node', pos=self.consume_cur().pos)
      
      case 'if':
        return self.parse_if_node()
      
      case 'return':
        return self.parse_return_node()
      
      case 'while':
        return self.parse_while_node()

      case 'break' | 'continue':
        return self.make_node(f'{self.cur.kind}_node', pos=self.consume_cur().pos)

      case 'for':
        return self.parse_for_node()
      
      case 'try':
        return self.parse_try_node()
      
      case 'defer':
        return self.parse_defer_node()
      
      case 'match':
        return self.parse_match_node()

      case _:
        if self.match_pattern(['id', ':'], allow_first_on_new_line=True):
          return self.parse_var_decl()
        
        stmt = self.parse_expr(allow_left_on_new_line=True) if not self.match_tok('..', allow_on_new_line=True) else self.consume_cur()
        
        if self.match_toks(['=', '+=', '-=', '*=']):
          op = self.consume_cur()
          expr = self.parse_expr()

          stmt = self.make_node(
            'assignment_node',
            lexpr=stmt,
            op=op,
            rexpr=expr,
            pos=op.pos
          )
        
        return stmt

  def parse_block(self):
    block = []
    self.expect_and_consume(':')

    if not self.cur.is_on_new_line:
      error('blocks cannot be inlined', self.cur.pos)

    if self.cur.indent <= self.cur_indent:
      error('invalid indent', self.cur.pos)

    self.indents.append(self.cur.indent)

    while True:
      block.append(self.stmt_parser_fn())

      if self.has_tok and not self.cur.is_on_new_line:
        error(f'unexpected token `{self.cur.kind}` at the end of a statement', self.cur.pos)

      if not self.has_tok or self.cur.indent < self.cur_indent:
        break

      if self.cur.indent > self.cur_indent:
        error('invalid indent', self.cur.pos)
    
    self.indents.pop()
    return block

  def parse_fn_node(self):
    # eating `fn`
    self.advance()

    name = self.expect_and_consume('id')
    args, generics = self.parse_fn_args()
    ret_type = self.parse_fn_ret_type()
    body = self.parse_block()

    return self.make_node(
      'fn_node',
      name=name,
      generics=generics,
      args=args,
      ret_type=ret_type,
      body=body,
      pos=name.pos
    )

  def parse_type_decl_node(self):
    pos = self.consume_cur().pos
    name = self.expect_and_consume('id')
    generics = self.parse_generics(use_fn_notation=False)
    self.expect_and_consume('=')
    type = self.parse_type()

    return self.make_node(
      'type_decl_node',
      name=name,
      generics=generics,
      type=type,
      pos=pos
    )
  
  def parse_import_ids(self):
    if self.match_tok('*'):
      return self.consume_cur()

    ids = []
    self.expect_and_consume('[')

    while True:
      if len(ids) == 0 and self.match_tok(']', allow_on_new_line=True):
        break

      name = self.expect_and_consume('id', allow_on_new_line=True)
      alias = name

      if self.match_tok('->'):
        self.advance()
        alias = self.expect_and_consume('id')

      ids.append(self.make_node('id_import_node', name=name, alias=alias, pos=alias.pos))

      if not self.match_tok(','):
        break

      self.advance()

    self.expect_and_consume(']', allow_on_new_line=True)
    return ids

  def parse_import_node(self):
    pos = self.consume_cur().pos
    path = self.expect_and_consume('str')
    self.expect_and_consume('import')
    ids = self.parse_import_ids()

    path.value = fix_package_path(path.value)

    return self.make_node(
      'import_node',
      path=path,
      ids=ids,
      pos=pos
    )
  
  def parse_test_node(self):
    pos = self.consume_cur().pos
    desc = self.expect_and_consume('str')
    body = self.parse_block()

    return self.make_node(
      'test_node',
      desc=desc,
      body=body,
      pos=pos
    )

  def parse_next_global(self):
    if not self.has_tok:
      return
    
    if not self.cur.is_on_new_line:
      error('global must be on a new line', self.cur.pos)
      
    if self.cur.indent != 0:
      error('global has bad indent', self.cur.pos)

    match self.cur.kind:
      case 'fn':
        node = self.parse_fn_node()

      case 'type':
        node = self.parse_type_decl_node()
      
      case 'from':
        node = self.parse_import_node()
      
      case 'id':
        node = self.parse_var_decl()
      
      case 'test':
        node = self.parse_test_node()

      case _:
        error(f'unexpected token `{self.cur.kind}` here', self.cur.pos)
    
    return node

def parse(toks):
  p = Parser(toks)
  r = []

  while p.has_tok:
    node = p.parse_next_global()

    if node is None:
      break
    
    r.append(node)
  
  return r