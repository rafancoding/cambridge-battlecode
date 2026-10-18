import cambc
from randomness import below

# Champion IDs to draft, in priority order. IDs run 0-10.
PICKS = [3, 7, 8]

def play():
    chosen = cambc.draft.current_draft(cambc.Team.ALLY)
    available = [pick for pick in range(11) if pick not in chosen]  # Released champions only.

    wanted = [pick for pick in PICKS if pick in available]
    choice = wanted[0] if wanted else available[below(len(available))]

    # Draft memory starts at byte zero and is copied to each champion.
    cambc.draft_memory.submit(b"hello world")
    cambc.draft.pick(choice)
