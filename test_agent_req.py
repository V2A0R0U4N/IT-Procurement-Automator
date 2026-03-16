from core.parser import parse_requirement, requirement_to_dict
from core.agent import ProcurementAgent

# Parser test
req = parse_requirement('I need an Asus or HP laptop for daily work. It must have an Intel Core i5 processor, 16GB of RAM, and at least 512GB SSD storage. My maximum budget is ₹60,000.')
req_dict = requirement_to_dict(req)
print(f"Parser Dict: {req_dict}")

