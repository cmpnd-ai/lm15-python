# Adaptations: switch providers, keep your program

lm15 makes two promises. First: change the model or provider string and
your program keeps working. Second: never change what you asked for. The
first comes first. The second is kept by **telling you**, not by stopping
you.

When a provider cannot take a setting exactly as you wrote it, lm15 does
the obvious thing and **records** it on the response:

```python
import lm15
from lm15 import Config, Message, Request

router = lm15.LMRouter()
response = router.complete(Request(
    model="anthropic:claude-sonnet-4-5",
    messages=(Message.user("Pick a number."),),
    config=Config(seed=42, temperature=1.5),
))

for a in response.adaptations:
    print(a.field, a.action, a.asked, "->", a.applied)
# config.max_tokens  defaulted  None -> 16384
# config.seed        dropped    42   -> None
# config.temperature clamped    1.5  -> 1.0
```

Nothing printed on its own. The record is data on the response; look at
it when you care, ignore it when you don't.

## What counts as an adaptation

Only something the wire got that differs from what you asked. Ordinary
translation — `stop` becoming `stop_sequences`, an effort word becoming a
thinking budget — is the adapter's job and is never recorded.

| Action | Meaning | Example |
|---|---|---|
| `dropped` | no home on this wire; left out | `seed` on Anthropic; `top_k` on OpenAI |
| `clamped` | a dial moved to its nearest level | `temperature=1.5` → `1.0` on Anthropic; `effort="xhigh"` → `"high"` on Gemini 3 |
| `substituted` | the closest spelling went instead | `summary="concise"` → `"auto"`; `reasoning="off"` → the lowest level where no off switch exists |
| `client_side` | lm15 does it after the wire | `stop` on the OpenAI Responses API (streamed and cut at the sequence, even on a plain call); a tool allowlist sent as only those tools |
| `satisfied` | the provider's default already is what you asked | `store=False` on Anthropic, which keeps no retrievable copy |
| `defaulted` | the wire requires a value you did not set | Anthropic `max_tokens` (the class ceiling, not a silent 1024) |

Each record carries `field` (the config path), `action`, `asked`,
`applied`, and a one-sentence `reason` naming the provider fact.

## What is still refused

lm15 refuses — `UnsupportedFeatureError` before anything is sent — only
when adapting would be a guess that could hurt:

- **a real choice is needed** — a thinking budget with no effort word:
  which effort is 4,000 tokens?
- **your program depends on it** — an image the wire cannot carry (the
  model would answer without seeing it), a stored cache object that does
  not exist on this provider, a tool the provider cannot run, `n > 1`;
- **nothing sensible to adapt to**;
- **a wrong guess would cost money, leak data, or go unnoticed**.

Every refusal names its field: `error.feature` is `"config.top_k"`,
`"messages[*].parts[image]"`, `"tools[web_search]"`, so a policy layer
can drop that one thing and retry without parsing the message.

## Preview without sending

```python
plan = router.plan(request)      # tuple[Adaptation, ...]; no network
```

`plan()` raises exactly what the call would raise. It is offline like
`resolve()`: no network, and no credential is read or invoked — a route
with no key still plans. Use it to decide between routes before spending
a request.

## The switch

```python
lm15.RouterConfig(adaptations="note")     # default: adapt and record
lm15.RouterConfig(adaptations="silent")   # adapt, record nothing
lm15.RouterConfig(adaptations="refuse")   # the strict mode
```

The same keyword exists on every LM constructor (`AnthropicLM(...,
adaptations="refuse")`). Under `"refuse"`, every deviation — `dropped`,
`clamped`, `substituted`, `client_side` — is an error before the wire.
`satisfied` and `defaulted` change nothing you asked for and are
recorded, not refused.

If you log adaptations, log each distinct one once per process. A note
that nags gets switched off, and then the drop is silent again.

## Streaming

The record is known before the first byte, so it rides the stream's
start event (`StreamStartEvent.adaptations`) and the assembled response
alike.

A client-side `stop` is honoured by **streaming and closing the
connection at the cut** — on a plain `complete()` call too, under the
hood. Generation stops there, so nothing past the sequence is billed.
The price: the usage report only rides the provider's final frame, which
is never read when the cut happens, so `usage` is "not reported" on
those calls — never estimated. A call whose text never reaches the
sequence completes normally, usage included.

## Where the rule lives

`lm15-contract/docs/mapping-rules.md` MAP-13 is the normative text; the
audit that applied it to every refusal in the reference is
`lm15-contract/changes/2026-09-14-adapt-visibly.md`.
