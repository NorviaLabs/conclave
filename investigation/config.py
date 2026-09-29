"""Application tuning: how many rounds, and how wide each round is.

Round-based orchestration is this application's decision, not the
framework's. The core runs one agent; the loop that decides what deserves
a round, and how many, lives here.
"""

from conclave import RunConfig


MAX_ROUNDS = 8

MAX_DELEGATIONS_PER_ROUND = 3


# One round fans out at most this widely, and never wider than the
# delegation guide the coordinator is given.
RUN_CONFIG = RunConfig(
    fan_out_limit=MAX_DELEGATIONS_PER_ROUND
)
