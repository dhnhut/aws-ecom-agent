# ===
# Test 1 — Order Tracking
echo "=================================="
echo "=== Test 1 — Order Tracking ==="
echo "Expected: shipping status, tracking number TRK987654321, carrier UPS, estimated delivery date."
echo "---"

agentcore invoke --prompt "Can you track order ORD-001?" --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000001
echo

# ===
# Test 2 — Refund Processing
echo "=================================="
echo "=== Test 2 — Refund Processing ==="
echo "Expected: refund ID, APPROVED status, \"3-5 business days\" message."
echo "---"

agentcore invoke --prompt "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund." --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000002
echo

# ===
# Test 3 — Knowledge Base (RAG)
echo "=================================="
echo "=== Test 3 — Knowledge Base (RAG) ==="
echo "Expected: free same-day shipping, 15% discount, priority support (retrieved from Knowledge Base)."
echo "---"
agentcore invoke --prompt "What are the benefits of the Platinum loyalty tier?" --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000003
echo

# ===
# Test 4 — Long-Term Memory (two sessions)
echo "=================================="
echo "=== Test 4 — Long-Term Memory (two sessions) ==="
echo "Expected: agent recalls previous interactions and maintains context across sessions."
echo "---"

# # Session A — introduce yourself
agentcore invoke --prompt "Hi, I am Jane. I prefer concise responses." --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000041


# # Wait at least 30 seconds for memory extraction
echo "Waiting for memory extraction..."
sleep 45
echo "Memory extraction complete."


# # Session B — verify recall (new session, same customer)
agentcore invoke --prompt "Do you remember my name and communication preference?" --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000042
echo

# ===
# Test 5 — Loyalty Discount Calculation
echo "=================================="
echo "=== Test 5 — Loyalty Discount Calculation ==="
echo "Expected: points redeemed, tier discount 10%, correct final total, remaining points"
echo "---"

agentcore invoke --prompt "I am a Gold loyalty tier member with 4250 points. Calculate my discount on a \$150 standard order." --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000005
echo

# ===
# Test 6 — Browser Tool
echo "=================================="
echo "=== Test 6 — Browser Tool ==="
echo "Expected: page title retrieved from the live udacity.com page"
echo "---"

agentcore invoke --prompt "Go to https://www.udacity.com and tell me the page title." --user-id CUST-123 --session-id 00000000-0000-0000-0000-000000000006