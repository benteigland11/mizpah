"""Estimate before the call, calibrate from the usage the provider reports after it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.hosted_token_estimator import HostedTokenEstimator

estimator = HostedTokenEstimator(characters_per_token=3.6)
payload = {"messages": [{"role": "system", "content": "You are a careful assistant." * 8},
                        {"role": "user", "content": "Summarise the attached notes in three bullets. " * 20}]}

print("before any exchange:", estimator.estimate(payload).as_dict())

# The provider answered and reported prompt_tokens=610 for that payload.
estimator.calibrate(payload, 610)
print("after one exchange:  ", estimator.estimate(payload).as_dict())

# A second exchange reported 640 for a slightly larger prompt.
payload["messages"].append({"role": "user", "content": "And keep it under fifty words."})
estimator.calibrate(payload, 640)
print("after two exchanges: ", estimator.estimate(payload).as_dict())
