# Demo script (about 3 minutes)
1. **Hook (20 s):** "An ASHA worker fills registers by hand. We turn one spoken sentence into a checked, saved record."
2. Open http://localhost:8000. Point out *Connected* and the demo-mode banner (be upfront: speech is simulated in mock mode).
3. Tap mic → "Today I visited Bharath." Show: no guessing; only IDs and villages shown.
4. Tap mic → "PAT002". Tap → "pregnancy checkup". Tap → headache and blurred vision + follow up in 3 days.
5. Show the red danger box and the fixed referral sentence (rule-based, no LLM). Say the rules are pending medical verification.
6. Read-back includes the ID. Tap **No**, type "no, it was a child checkup", show the correction, then tap **Yes, save**. Tap Yes again: "already saved".
7. Show follow-up list. Mention Fernet encryption and visible metadata.
8. Kill Wi-Fi briefly: status shows reconnecting; resume without duplicates.
9. After judging: `python scripts/delete_all_data.py --yes`.
