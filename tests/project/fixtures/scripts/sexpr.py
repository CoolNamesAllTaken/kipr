"""Tiny helpers for surgical edits of KiCad s-expression files (text level, so the
diff between base and head stays minimal)."""
import re
import uuid as _uuid


def block_at(s, start):
    """Return the balanced '(...)' substring starting at s[start] == '('."""
    i, depth = start, 0
    while True:
        c = s[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        elif c == '"':
            i += 1
            while s[i] != '"':
                if s[i] == '\\':
                    i += 1
                i += 1
        i += 1


def top_blocks(s, head):
    """Yield (start, text) for every top-level (tab-indented) block '(<head>' in a file."""
    for m in re.finditer(r'\n\t\(' + re.escape(head) + r'[\s\n]', s):
        yield m.start() + 2, block_at(s, m.start() + 2)


def find_symbol(s, ref):
    for start, b in top_blocks(s, 'symbol'):
        if re.search(r'\(property "Reference" "%s"' % re.escape(ref), b):
            return start, b
    raise KeyError(ref)


def remove_block(s, start, b):
    """Remove a top-level block together with its leading tab and trailing newline."""
    assert s[start:start + len(b)] == b
    return s[:start - 1] + s[start + len(b) + 1:]


def new_uuids(b, seed):
    """Replace every uuid in a block by a deterministic new one derived from seed."""
    n = [0]

    def rep(m):
        n[0] += 1
        return '(uuid "%s")' % _uuid.uuid5(_uuid.NAMESPACE_URL, '%s/%d' % (seed, n[0]))
    return re.sub(r'\(uuid "[0-9a-f-]+"\)', rep, b)


def shift_at(b, dx, dy):
    """Translate every '(at x y [r])' in a block."""
    def rep(m):
        x, y = float(m.group(1)) + dx, float(m.group(2)) + dy
        return '(at %s %s%s)' % (fmt(x), fmt(y), m.group(3) or '')
    return re.sub(r'\(at (-?[\d.]+) (-?[\d.]+)((?: -?[\d.]+)?)\)', rep, b)


def fmt(v):
    v = round(v, 4)
    return ('%f' % v).rstrip('0').rstrip('.') if v != int(v) else str(int(v))


def set_ref(b, old, new):
    b = b.replace('(property "Reference" "%s"' % old, '(property "Reference" "%s"' % new)
    return b.replace('(reference "%s")' % old, '(reference "%s")' % new)


def set_prop(b, name, value):
    b2 = re.sub(r'\(property "%s" "[^"]*"' % re.escape(name), '(property "%s" "%s"' % (name, value), b, count=1)
    assert b2 != b or ('(property "%s" "%s"' % (name, value)) in b, name
    return b2


def insert_before(s, marker, text):
    i = s.index(marker)
    return s[:i] + text + s[i:]
