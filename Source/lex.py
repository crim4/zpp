from data import Node
from utils import error

SKIPPABLE = [' ', '\n', '\t', '\\']
DOUBLE_PUNCTUATION = ['==', '->', '..', '+=', '-=', '*=', '!=', '<=', '>=']
KEYWORDS = [
  'fn', 'pass', 'if', 'elif', 'else', 'return',
  'Undefined', 'True', 'False', 'None',
  'type', 'while', 'break', 'continue', 'mut',
  'for', 'import', 'and', 'or',
  'not', 'try', 'out', 'from', 'defer', 'test',
  'match', 'case', 'cast',
  'ref'
]

class Lexer:
  def __init__(self, src, path):
    self.src = src
    self.path = path
    self.index = 0
    self.indent = 0
    self.on_new_line = True
    self.line = 1
    self.start_line_index = 0
  
  @property
  def has_char(self):
    return self.index < len(self.src)
    
  @property
  def has_next_char(self):
    return self.index + 1 < len(self.src)

  @property
  def nxt(self):
    return self.src[self.index + 1]

  @property
  def cur(self):
    return self.src[self.index]

  @property
  def bck(self):
    return self.src[self.index - 1]

  @property
  def col(self):
    return self.index - self.start_line_index + 1

  @property
  def cur_pos(self):
    return (self.line, self.col, self.src, self.path)

  def advance(self, count=1):
    self.index += count

  def skip(self):
    is_collecting_inline_comment = False

    while self.has_char and (is_collecting_inline_comment or self.cur in SKIPPABLE or self.cur == '-'):
      if is_collecting_inline_comment and self.cur != '\n':
        self.advance()
        continue

      match self.cur:
        case '\n':
          self.line += 1
          self.indent = 0
          self.start_line_index = self.index + 1
          self.on_new_line = True

          is_collecting_inline_comment = False

        case ' ':
          self.indent += 1
        
        case '\\':
          if self.on_new_line:
            error('token `\\` can only be used as last character of the line', self.cur_pos)
          
          # skipping `\`
          self.advance()
          
          while self.has_char and self.cur in [' ', '\t']:
            self.advance()
          
          if not self.has_char or self.cur != '\n':
            error('expected token on new line after `\\`', self.cur_pos)
          
          self.indent = 0
          self.line += 1
          self.start_line_index = self.index + 1
          self.on_new_line = False
        
        case '\t':
          error('tab illegal', self.cur_pos)
        
        case '-':
          if not self.has_next_char or self.nxt != '-':
            break
          
          is_collecting_inline_comment = True
        
        case _:
          raise NotImplementedError()

      self.advance()

  def consume_is_on_new_line(self):
    t = self.on_new_line
    self.on_new_line = False
    
    return t
  
  def consume_indent(self):
    t = self.indent
    self.indent = 0
    
    return t
  
  def collect_word(self):
    t = ''
    p = self.cur_pos
    is_digit = self.cur.isdigit()
    is_ident = not is_digit

    while self.has_char:
      if is_ident and (self.cur.isalnum() or self.cur == '_'):
        pass
      elif is_digit and (self.cur.isdigit() or self.cur in ["'", '.']):
        pass
      else:
        break

      t += self.cur
      self.advance()
    
    self.advance(-1)

    if is_digit:
      new_t = t.replace("'", '')

      if t.endswith('.') or t.endswith("'"):
        self.advance(-1)
        new_t = new_t.rstrip(".'")

      try:
        _ = float(new_t)
        failed_parsing = False
      except ValueError:
        failed_parsing = True

      if (
          ".'" in new_t or "'." in new_t or "''" in new_t \
          or new_t.count('.') > 1 or failed_parsing
      ):
        error('malformed num', p)

      kind = 'fnum' if '.' in new_t else 'num'
      t = new_t
    else:
      kind = t if t in KEYWORDS else 'id'

    return self.make_tok(
      kind,
      value=t,
      pos=p
    )
  
  def make_tok(self, kind, **kwargs):
    assert 'pos' in kwargs

    return Node(
      kind,
      indent=self.consume_indent(),
      is_on_new_line=self.consume_is_on_new_line(),
      **kwargs
    )

  def collect_punctuation(self):
    k = self.cur
    p = self.cur_pos

    if self.has_next_char and k + self.src[self.index + 1] in DOUBLE_PUNCTUATION:
      self.advance()
      k += self.cur

    return self.make_tok(k, value=k, pos=p)

  def get_escaped_char_value(self, c):
    try:
      return {
        'n': '\n',
        'r': '\r',
        't': '\t',
        '0': '\0',
        "'": "'",
        '`': '`'
      }[c]
    except KeyError:
      error('unknown escaped char', self.cur_pos)

  def collect_str_or_chr(self):
    p = self.cur_pos
    apex = self.cur
    is_str = apex == "'"
    kind = 'str' if is_str else 'chr'
    self.advance()
    t = ''

    while self.has_char and self.cur != apex:
      c = self.cur

      if self.cur == '\\':
        self.advance()

        if not self.has_char:
          break
        
        c = self.get_escaped_char_value(self.cur)

      t += c
      self.advance()
    
    if not self.has_char:
      error(f'malformed {kind}', p)
    
    if not is_str and len(t) != 1:
      error('malformed chr (1 character expected)', p)
    
    return self.make_tok(
      kind,
      value=t,
      pos=p
    )

  def gen_next(self):
    self.skip()

    if not self.has_char:
      return
    
    if self.cur.isalnum() or self.cur == '_':
      t = self.collect_word()
    elif self.cur in ["'", '`']:
      t = self.collect_str_or_chr()
    else:
      t = self.collect_punctuation()
    
    self.advance()
    return t

def lex(src, path):
  l = Lexer(src, path)
  r = []
  
  while l.has_char:
    tok = l.gen_next()
    
    if tok is None:
      break

    r.append(tok)
  
  return r