"""One bounded AND/comparison grammar for query selection and source events."""

import ast
import operator

OPS = {
	ast.Eq: ("=", operator.eq),
	ast.NotEq: ("!=", operator.ne),
	ast.Lt: ("<", operator.lt),
	ast.LtE: ("<=", operator.le),
	ast.Gt: (">", operator.gt),
	ast.GtE: (">=", operator.ge),
	ast.In: ("in", lambda a, b: a in b),
	ast.NotIn: ("not in", lambda a, b: a not in b),
}


class ConditionError(ValueError):
	pass


def parse(expression, allowed_fields):
	if expression is None or expression == "":
		return []
	if not isinstance(expression, str) or len(expression) > 4096:
		raise ConditionError("Conditions must be a short comparison expression")
	if not expression.strip():
		return []
	try:
		tree = ast.parse(expression, mode="eval")
	except (SyntaxError, RecursionError):
		raise ConditionError("Invalid streaming condition syntax") from None
	if sum(1 for _ in ast.walk(tree)) > 128:
		raise ConditionError("Streaming condition is too complex")
	parts = (
		tree.body.values
		if isinstance(tree.body, ast.BoolOp) and isinstance(tree.body.op, ast.And)
		else [tree.body]
	)
	result = []
	for part in parts:
		if not isinstance(part, ast.Compare) or len(part.ops) != 1 or type(part.ops[0]) not in OPS:
			raise ConditionError("Use doc.field comparisons joined by and")
		left = part.left
		if (
			not isinstance(left, ast.Attribute)
			or not isinstance(left.value, ast.Name)
			or left.value.id != "doc"
			or left.attr not in allowed_fields
			or left.attr.startswith("_")
		):
			raise ConditionError("Conditions can reference only approved doc fields")
		right = part.comparators[0]
		if isinstance(right, (ast.List, ast.Tuple)):
			if len(right.elts) > 100 or any(not isinstance(item, ast.Constant) for item in right.elts):
				raise ConditionError("Use a short list of literal values")
			value = [item.value for item in right.elts]
		elif isinstance(right, ast.Constant):
			value = right.value
		else:
			raise ConditionError("Conditions require literal comparison values")
		values = value if isinstance(value, list) else [value]
		if any(item is not None and not isinstance(item, (str, int, float, bool)) for item in values):
			raise ConditionError("Unsupported comparison value")
		kind = type(part.ops[0])
		if (kind in (ast.In, ast.NotIn)) != isinstance(value, list):
			raise ConditionError("in and not in require a literal list")
		if value is None and kind not in (ast.Eq, ast.NotEq):
			raise ConditionError("None supports only == and !=")
		result.append((left.attr, kind, value))
	return result


def filters(parsed):
	return [
		[name, "is", "not set" if kind == ast.Eq else "set"] if value is None else [name, OPS[kind][0], value]
		for name, kind, value in parsed
	]


def matches(parsed, document):
	for name, kind, expected in parsed:
		actual = document.get(name)
		if expected is None:
			matched = actual in (None, "")
			if not matched if kind == ast.Eq else matched:
				return False
			continue
		if actual is None:
			actual = "" if isinstance(expected, (str, list)) else 0
		try:
			if not OPS[kind][1](actual, expected):
				return False
		except (TypeError, ValueError):
			return False
	return True
