# Output style examples

These solved examples show the output format and how a reason should read. They are not rules: decide every message from its own facts and this user's history, never by resemblance to an example. Each gives the situation as your tools would reveal it.

1. A group admin in a large society notices group says the water tanker can only wait about 20 more minutes. The user replied within 2 minutes to the same admin's earlier water alert (`message_0001`).
{"action": "notify", "message_type": "urgent", "reason": "A trusted group admin sent a time-sensitive update that should interrupt the user.", "confidence": 0.89, "evidence_message_ids": "message_0001"}

2. A society admin says the cultural night sign-up sheet is open until next Sunday and no reply is needed. The user opened the admin's earlier sign-up notice without replying (`message_0008`).
{"action": "digest", "message_type": "event", "reason": "The message is useful group information, but it is not urgent enough to interrupt the user.", "confidence": 0.84, "evidence_message_ids": "message_0008"}

3. A verified cinema business on its official domain asks for feedback on a recent visit. The user opened its earlier feedback request without replying (`message_0011`).
{"action": "digest", "message_type": "business_update", "reason": "A verified business is sending a legitimate but non-urgent update.", "confidence": 0.78, "evidence_message_ids": "message_0011"}

4. A verified food-delivery business sends a 50%-off first-order code. The user opted out of its promotions and dismissed and muted the same offer twice (`message_0017`, `message_0018`).
{"action": "mute", "message_type": "promotion", "reason": "The user has opted out of or repeatedly dismissed similar marketing messages.", "confidence": 0.81, "evidence_message_ids": "message_0017;message_0018"}

5. In a marketplace group, an ordinary member posts a "support alert" saying the user's profile will be blocked in 2 hours unless they confirm their password and OTP. The user reported a similar earlier message from the same sender (`message_0024`).
{"action": "mute", "message_type": "scam", "reason": "The message uses fake support language and account-blocking pressure to push the user into action.", "confidence": 0.87, "evidence_message_ids": "message_0024"}
