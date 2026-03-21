"""
tests/fixtures/agent_generated.py — Realistic examples of agent code quality problems.

These are the patterns Karpathy complained about:
  - Chained calls with subscript indexing
  - Copy-pasted blocks
  - Bloated unnecessary abstractions
  - Complex one-liners
"""

# ============================================================
# Pattern 1: Karpathy's exact complaint
# "calls 2 functions and then indexes an array with the result"
# ============================================================

def fetch_first_user(client, query):
    # Agent wrote this — one line doing 3 things
    return format_user(client.search(query)["results"][0])


def fetch_first_result(client, query):
    # Agent copy-pasted and adapted — structurally identical to fetch_first_user
    return format_result(client.search(query)["results"][0])


# ============================================================
# Pattern 2: Bloated abstractions
# Agent creates intermediate variables that add no structural value
# ============================================================

def process_response(response):
    temp_data = response          # just an alias — zero structural value
    temp_result = temp_data       # another alias — even more useless
    processed = temp_result       # still just the original response
    return processed


# ============================================================
# Pattern 3: Complex one-liners that normalize well
# ============================================================

def extract_names(data):
    # Agent wrote one line doing 4 operations
    return [item["name"].strip().lower() for item in data["items"] if item["active"]]


# ============================================================
# Pattern 4: What GOOD code looks like (Karpathy style)
# This should produce zero or minimal findings
# ============================================================

def fetch_first_user_clean(client, query):
    search_results = client.search(query)
    results_list = search_results["results"]
    first_result = results_list[0]
    formatted = format_user(first_result)
    return formatted
