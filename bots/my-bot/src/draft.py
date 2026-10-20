import cambc
from randomness import below

# 4
# 8
# 
PICKS = [9, 10, 11]

def play():
    chosen = cambc.draft.current_draft(cambc.Team.ALLY)
    available = [pick for pick in range(11) if pick not in chosen]

    wanted = [pick for pick in PICKS if pick in available]
    choice = wanted[0] if wanted else available[below(len(available))]

    # Draft memory starts at byte zero and is copied to each champion.
    cambc.draft_memory.submit(b"hello world")
    cambc.draft.pick(choice)
