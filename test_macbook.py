from core.parser import parse_requirement, requirement_to_dict
from core.parser import build_search_queries

req = parse_requirement('need MacBook m4 512 gb ssd')
req_dict = requirement_to_dict(req)
print(f"Parsed: {req_dict}")
print(f"Queries: {build_search_queries(req)}")
