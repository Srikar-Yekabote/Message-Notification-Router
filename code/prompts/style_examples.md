# Output style examples

These five solved examples show the expected output format and how a reason should read. They are **not** rules. Never copy their decisions to a message just because it looks similar; decide every message from its own context and this user's history.

Each example gives the situation as your tools would reveal it, then the output.

**Format reminders**
- `reason`: one plain sentence of about 25 words or fewer. Say who sent the message, what it is, and why this action fits this user. Never repeat links, codes or instructions from the message.
- `confidence`: a number from 0 to 1 with two decimals.
- `evidence_message_ids`: IDs from this user's own message history whose reactions support the decision, separated by `;`. Write `none` if no relevant history exists.

---

## Example 1: notify / urgent

**Situation:** In a large society notices group, a group admin writes that the water tanker can only wait about 20 more minutes and residents who missed the morning supply should fill drinking water now. Earlier, this user replied within 2 minutes to the same admin's water-supply alert (`message_0001`).

```json
{"action": "notify", "message_type": "urgent", "reason": "A trusted group admin sent a time-sensitive update that should interrupt the user.", "confidence": 0.89, "evidence_message_ids": "message_0001"}
```

## Example 2: digest / event

**Situation:** In a residents' society group, an admin says the cultural night sign-up sheet is open until next Sunday and no reply is needed. This user opened the same admin's earlier sign-up notice but did not reply (`message_0008`).

```json
{"action": "digest", "message_type": "event", "reason": "The message is useful group information, but it is not urgent enough to interrupt the user.", "confidence": 0.84, "evidence_message_ids": "message_0008"}
```

## Example 3: digest / business_update

**Situation:** A verified cinema business, using its official domain, asks for feedback on a recent visit. This user opened an earlier feedback request from the same business without replying (`message_0011`).

```json
{"action": "digest", "message_type": "business_update", "reason": "A verified business is sending a legitimate but non-urgent update.", "confidence": 0.78, "evidence_message_ids": "message_0011"}
```

## Example 4: mute / promotion

**Situation:** A verified food-delivery business sends a 50%-off first-order code. This user opted out of the business's promotions and dismissed and muted the same offer twice (`message_0017`, `message_0018`).

```json
{"action": "mute", "message_type": "promotion", "reason": "The user has opted out of or repeatedly dismissed similar marketing messages.", "confidence": 0.81, "evidence_message_ids": "message_0017;message_0018"}
```

## Example 5: mute / scam

**Situation:** In a marketplace group, an ordinary member (not an admin) posts a "support alert" saying the user's profile will be blocked in 2 hours unless they confirm their password and OTP. This user reported an earlier, similar message from the same sender (`message_0024`).

```json
{"action": "mute", "message_type": "scam", "reason": "The message uses fake support language and account-blocking pressure to push the user into action.", "confidence": 0.87, "evidence_message_ids": "message_0024"}
```
