#!/usr/bin/env python

from collections import namedtuple
import lark
import operator
import os
import sys

parser = lark.Lark.open('grammar.lark')

program_a = \
"""
fib 0 = 0

fib 1 = 1

a n = [fib n-1] + [fib n-2]

fib n = [a n]

bar m n = [fib n]

foo n = (n * 3 + (n-1) * 4) / 2

zar x y = (x * y)/(x + y * ([foo x] + [foo y]))

RUN [fib 10] [bar 0 1] [foo 7] [zar 8 6]
"""

program_cycle = \
"""
cycle x = [bicycle x-1]

bicycle x = [cycle x+1]

RUN [cycle 199]
"""

program_nested_ref = \
"""
a x = x
b x = 1

foo x = [a [b x]]

RUN [foo 1]
"""

program_nat_bad = \
"""
nat n = [nat n-1] + 1

nat 0 = 0

RUN [nat 10]
"""


def test():
  parse(program_a, debug=True)


def test_refs():
  node_list, _ = parse(program_a, debug=True)

  node_refs = [find_references_no_nesting(n.expr) for n in node_list]

  for n,refs in zip(node_list, node_refs):
    print(f"Node {n} references {refs}")


def test_run():
  node_list, run_list = parse(program_a, debug=True)

  print(f"run_list contains: ")
  for n in run_list:
    print(n)

  result = run(node_list, run_list, debug=True)

  # for k, v in result.items():
  #   print(f"{k} -> {v}")

  for n in run_list:
    print(f"{n} -> {result[n]}")


def test_cycle():
  node_list, run_list = parse(program_cycle, debug=True)

  try:
    run(node_list, run_list, debug=True)
    assert(False)
  except Exception as e:
    assert("Found cyclic dependency" in str(e))


def test_nested_ref():
  node_list, run_list = parse(program_nested_ref, debug=True)

  try:
    run(node_list, run_list, debug=True)
    assert(False)
  except:
    # TODO EDF need to make these work
    pass


def test_pattern_order():
  pass
  # # will run forever
  # node_list, run_list = parse(program_nat_bad, debug=True)

  # run(node_list, run_list, debug=True)



def parse(text, debug=False):
  tree = parser.parse(text)
  #print(f"Tree:\n{tree.pretty()}")
  node_patterns = MyTransformer().transform(tree)
  
  # TODO EDF check that all patterns of the same name have the same
  # number of parameters

  node_list = node_patterns.children[0]
  run_list = node_patterns.children[1]

  if debug:
    print("Node patterns:")
    for pat in node_list:
      print(f"\t{pat}")

    print()
    print("Nodes to run:")    
    for n in run_list:
      print(f"\t{n}")      

  return node_list, run_list


NodePattern = namedtuple("NodePattern", "name params expr")

NodeReference = namedtuple("NodeReference", "name args")

NodeToRun = namedtuple("NodeToRun", "name args")


def find_pattern(node_to_run, node_list):
  args = node_to_run.args
  for pat in node_list:

    if pat.name != node_to_run.name:
      continue

    assert(len(pat.params) == len(args))

    okay = True

    for arg, param in zip(args, pat.params):
      assert(type(arg) is int)      

      if type(param) is str:
        continue # can always pattern match on variable name

      if arg != param:
        okay = False
        break

    if okay:
      return pat
  
  return None
      

def find_references_no_nesting(t):
  if isinstance(t, NodeReference):
    return [t]

  if isinstance(t, lark.Tree):
    return [r for c in t.children for r in find_references_no_nesting(c)]

  return []


def find_and_bind_pattern(node_to_run, node_list):
  """
  Pattern match the node_to_run to a given node pattern.

  The pattern matching here is very simple:
  * a variable name in the pattern's args matches any value
  * an int value in the pattern's args only matches that int value

  Returns a tuple containing the matched pattern and variable bindings.
  """
  pat = find_pattern(node_to_run, node_list)

  if pat == None:
    raise Exception(f"Unable to find pattern for {node_to_run}")

  # variable bindings to use when evaluating expressions
  bindings = {p:a for p, a in zip(pat.params, node_to_run.args)}

  return pat, bindings


def compute_dependencies(node_to_run, node_list):
  """
  """

  pat, bindings = find_and_bind_pattern(node_to_run, node_list)

  # now look for other references in the expression
  refs = find_references_no_nesting(pat.expr)

  transformer = EvalTransformer({}, {}, bindings)

  deps = []

  for name, args in refs:
    evald_args = tuple(transformer.eval(expr) for expr in args)
    deps.append(NodeToRun(name, evald_args))

  return refs, deps


def run(node_list, run_list, debug=False):
  # computation result; populated in post-order traversal
  result = {} # populated in post-order

  # pending computations for cycle detection; populated in pre-order
  # a node n will be in pending iff ('post', n, _) is in the frontier
  pending = set()

  # do depth-first traversal of the node dependency DAG
  frontier = [('pre', n, None) for n in run_list]
  while len(frontier) > 0:
    p, node_to_run, refs_to_deps = frontier.pop()

    if debug:
      print(f"popped: {p, node_to_run, refs_to_deps}")

    assert(p in ['pre', 'post'])

    if p == 'pre':
      # already computed; don't spend time on it again
      if node_to_run in result:
        continue

      # cycle avoidance
      if node_to_run in pending:
        raise Exception(f"Found cyclic dependency at {node_to_run}")
      pending.add(node_to_run)

      # add dependencies to frontier
      refs, deps = compute_dependencies(node_to_run, node_list)

      push = [('post', node_to_run, {r: d for r,d in zip(refs, deps)})] + [('pre', dep, None) for dep in deps]
        
      if debug:
        for v in push:
          print(f" pushed: {v}")

      frontier += push

    elif p == 'post':
      # need to remove the node from pending here, as it's valid for the node to be added to the frontier
      # again, although at that time, dependencies won't be added as this node will already exist in the
      # result map
      pending.remove(node_to_run)

      # at this point we should be able to evaluate the node as all nodes on which we depend should already
      # be computed
      pat, bindings = find_and_bind_pattern(node_to_run, node_list)
      value = EvalTransformer(refs_to_deps, result, bindings).eval(pat.expr)

      # track result
      result[node_to_run] = value

  return result


class EvalTransformer(lark.Transformer):
  def __init__(self, node_refs_to_deps, node_result, var_bindings):
    self.node_refs_to_deps = node_refs_to_deps
    self.node_result = node_result
    self.var_bindings = var_bindings  

  def _lookup(self, item):
    if isinstance(item, int):
      return item

    if isinstance(item, NodeReference):
      try:
        # turn into NodeToRun & lookup in following if    
        item = self.node_refs_to_deps[item]
      except:
        raise Exception(f"No result for {item}; possible nested reference")

    if isinstance(item, NodeToRun):
      try:
        return self.node_result[item]
      except:
        raise RuntimeError(f"No result for {item}")

    try:
      return self.var_bindings[item]
    except:
      raise RuntimeError(f"Unrecognized item {item} of type {type(item)}")

  def eval(self, something):
    # TODO EDF kind of a nasty hack...figure out better way to avoid calling this 
    # eval() instead of directly calling transform()
    if isinstance(something, lark.Tree):
      return self.transform(something)
    
    return self._lookup(something)

  def _do_op(self, items, op):
    return op(self._lookup(items[0]), self._lookup(items[1]))

  def add(self, items):
    return self._do_op(items, operator.add)

  def sub(self, items):
    return self._do_op(items, operator.sub)

  def mul(self, items):
    return self._do_op(items, operator.mul)

  def div(self, items):
    return self._do_op(items, operator.floordiv)  

  def mod(self, items):
    return self._do_op(items, operator.mod)


class MyTransformer(lark.Transformer):
  def name(self, items):
    return str(items[0])

  def int(self, items):
    return int(items[0])

  def node_list(self, items):
    return tuple(items)

  def node(self, items):
    sig, expr = items
    name = sig.children[0]
    params = sig.children[1:]

    return NodePattern(name, params, expr)

  def param_name(self, items):
    return str(items[0])

  def node_reference(self, items):
    name = items[0]
    args = tuple(items[1:])

    return NodeReference(name, args)

  def run_list(self, items):
    return list(items)

  def node_to_run(self, items):
    name = items[0]
    args = tuple(items[1:])

    return NodeToRun(name, args)


def run_program(program_text):
  node_list, run_list = parse(program_text)

  result = run(node_list, run_list)

  for n in run_list:
    print(f"{n} -> {result[n]}")


if __name__ == '__main__':
  if False:
    test()
    test_refs()
    test_cycle()
    test_nested_ref()
    test_pattern_order()
    test_run()

  path  = sys.argv[1]

  if not os.path.exists(path):
    raise Exception(f"file {path} doesn't exist")

  with open(path) as f:
    program_text = f.read()

  run_program(program_text)

  