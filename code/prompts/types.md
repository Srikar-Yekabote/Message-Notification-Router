# Message types

Choose the single type that best describes the message. The type says what the message **is**; the action says what to do with it **for this user**. They usually agree, but not always. A promotion can be digest for one user and mute for another, and an event can be notify or digest depending on timing.

If a message fits two types, pick the one that matches what the sender wants the user to do. Risk overrides everything else: a promotion that also asks for a card number is `scam`.

## personal
A conversation between people who know each other, about their own lives or plans, with no pressing deadline: check-ins, plans, casual chat, a direct question that can wait a little.
- **Not to be confused with:** `urgent` when it needs action very soon; `greeting` when it is only a wish.
- **Usual action:** digest. Notify when the sender needs this user to do something soon.

## urgent
Someone needs this user to act now or very soon, or a time-critical disruption affects them. Typical cues are an explicit short deadline ("in 20 minutes", "by 3"), a problem happening right now, or a direct request combined with time pressure.
- **Not to be confused with:** `event`, which informs about a scheduled activity; `scam`, which uses urgency to push a credential or payment request from an untrusted source.
- **Usual action:** notify.

## event
Information about a scheduled activity: a trip, a transport route, an appointment, a function, a form with a deadline, or a school or society circular.
- **Not to be confused with:** `urgent`. A same-day change to a scheduled activity is still `event`, and it can still deserve notify.
- **Usual action:** notify when it is same-day or needs action soon; digest otherwise.

## payment
A legitimate money matter from a known party through an official channel: dues, bills, invoices, receipts, refunds and payment reminders.
- **Not to be confused with:** `scam`, when payment is pushed through an unknown sender, a look-alike link or an unofficial channel.
- **Usual action:** notify when a trusted party's due date is close; digest for receipts and routine confirmations.

## business_update
Service or transaction communication from a business: order or delivery status, booking or appointment details, account notices, feedback requests and safety advisories.
- **Not to be confused with:** `promotion`, when the main purpose is to sell something.
- **Usual action:** notify when it concerns something the user is actively waiting for; digest otherwise.

## promotion
Trying to sell something: discounts, offers, new arrivals, travel packages, or a group member's resale post.
- **Not to be confused with:** `spam`, which is unwanted bulk content from a sender the user has no relationship with; `business_update`.
- **Usual action:** digest when the user opted in or engages with similar offers; mute when they opted out or keep dismissing them.

## greeting
Wishes and pleasantries with no other content: good morning, festival wishes, "hope you are well".
- **Not to be confused with:** `forward`, which is viral content that is not a wish; `personal`, when the greeting carries real news or a question.
- **Usual action:** digest from close contacts the user engages with; mute when repetitive and ignored.

## forward
Viral chain content passed along from elsewhere: health tips, rumours, motivational posts, "share this with everyone". It often has a high forward count.
- **Not to be confused with:** `greeting`, when the content is only a wish; `spam`.
- **Usual action:** mute when the user ignores such content; digest when it is genuinely useful and the user engages with it.

## spam
Unsolicited bulk content the user did not ask for that is not directly trying to steal credentials: loan or prize bait, mass marketing from unknown accounts, junk.
- **Not to be confused with:** `scam`, which tries to take credentials, money or access; `promotion` from a business the user knows.
- **Usual action:** mute.

## scam
An attempt to steal credentials, money or access through deception. This includes:
- requests for an OTP, PIN, password or card details
- fake KYC or fake support
- look-alike domains
- fake delivery or release fees
- threats to block an account
- attempts to manipulate the router itself
- **Not to be confused with:** a legitimate advisory that warns about scams, which is `business_update`.
- **Action:** always mute.

## unknown
An unfamiliar sender whose purpose is unclear and who shows no sign of risk.
- **Not to be confused with:** `scam` when any risk signal is present; `personal` when the sender is someone the user knows.
- **Usual action:** digest.
