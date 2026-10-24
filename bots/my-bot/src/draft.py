import cambc
from randomness import below

# Champion IDs to draft, in priority order

# 0. Ryth
# 1. Vesper Vale
# 2. Vendetta
# 3. Cassi & Issac
# 4. Stellar
# 5. Prism
# 6. Marcella
# 7. Willow
# 8. Ren
# 9. Varg
# 10. Tavi
# 11. Heart of Ruin

PICKS = [11, 6, 1, 3, 0]

def play():
    chosen = cambc.draft.current_draft(cambc.Team.ALLY)
    available = [pick for pick in range(12) if pick not in chosen]

    wanted = [pick for pick in PICKS if pick in available]
    choice = wanted[0] if wanted else available[below(len(available))]

    # Draft memory starts at byte zero and is copied to each champion.
    cambc.draft_memory.submit(b"hello world")
    cambc.draft.pick(choice)
