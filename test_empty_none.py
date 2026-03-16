from core.parser import ProcurementRequirement, requirement_to_dict
import json
req = ProcurementRequirement(screen_size_inches=None)
d = requirement_to_dict(req)
print(d)
