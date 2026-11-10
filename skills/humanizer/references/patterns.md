# Humanizer patterns, detailed

Trigger phrases and fixes for each pattern in `SKILL.md`. §1 to §5 justify an edit on one sighting; *weak alone* patterns need other tells nearby.

## A. Staging instead of stating (act on one sighting)

1. **Not X but Y.** not just/only/merely X but Y; it's not X, it's Y; X rather than Y; split across sentences ("This does not mean X. It means Y."); clipped tail (", no guessing"). Any language. State the point directly. Keep a contrast only if the negative half corrects a belief the reader holds or both halves carry information.
2. **One-line closers and fragments.** A one-sentence paragraph restating the previous one; "That is the real win."; "Let that sink in."; the same closer after several sections; a row of fragments ("No prior. No nostalgia."); ALL CAPS or every. single. word. Cut repeating closers; merge fragments into one specific sentence.
3. **Sayings that sound deep.** the real question is, at its core, what really matters, fundamentally, the heart of the matter, X is the Y of Z, X becomes a trap, not a tool but a mirror, the language/currency/architecture of. Replace with the specific claim.
4. **Staged run-up.** Let's dive in, let's break this down, here's what you need to know, without further ado, quick note, Honestly?, Look, Here's the thing, Real talk. Remove the run-up and make the point. "Honestly" inside a casual sentence is fine.
5. **Arguing with no one.** This isn't about, I'm not saying, To be clear, Don't get me wrong, A tempting approach would be, You might think... but. Remove defenses against objections that appear nowhere else; keep real claims and options a reader would weigh.

## B. Rhythm by rule

6. **Forced triads.** Threes to sound complete (one sentence, three examples, or three facts plus a lesson). Merge, develop the strongest, or vary. Keep three real items.
7. **Repeated openings.** Several sentences in a row starting with the same subject. Merge or start with the action; deliberate rhythm is fine.
8. **Dashes.** The final text has no em dashes, en dashes, or ` -- ` used as dashes, unless the writer's sample uses them (then match its rate). Use a period, comma, colon, or parentheses. Leave code, commands, paths, URLs alone. One dash in someone's draft is *weak alone*.
9. **Stacked qualifiers.** could potentially, might arguably, in some cases it may. Keep one only when the source supports the doubt; keep scope, legal, and safety notices. *Weak alone.*
10. **Hyphenated pairs everywhere.** third-party, data-driven, high-quality, real-time, long-term. Hyphen before a noun, none after ("the report is high quality"). *Weak alone.*
11. **Passive voice, missing subjects.** "No configuration file needed." Name the actor when it is clearer. *Weak alone.*

## C. Inflation and borrowed authority (keep the fact, drop the dressing)

12. **Overused AI words.** Actually, additionally, align with, bolstered, crucial, deep dive, delve, emphasizing, enduring, enhance, fostering, garner, gate/gating (figurative), highlight (verb), interplay, intricate, key (adjective), landscape (abstract), meticulous, pivotal, quietly, robust (figurative), showcase, tapestry, testament, underscore (verb), valuable, vibrant. The only word list here; other formal words are not tells alone.
13. **Inflated significance.** stands as a testament, pivotal moment, plays a key role, marking/shaping the, reflects a broader, lasting legacy, setting the stage, evolving landscape; "Despite these challenges... continues to thrive"; stock Challenges/Future Outlook sections; the future looks bright. End on the last concrete fact.
14. **Vague association.** associated with, in connection with, linked to, tied to. Name the relationship the source gives; if none, keep it vague rather than invent.
15. **Shallow -ing riders.** highlighting, underscoring, ensuring, reflecting, symbolizing, contributing to, fostering, showcasing bolted onto a fact. Keep only if the source supports it.
16. **Sales language.** boasts, vibrant, rich, profound, nestled, in the heart of, groundbreaking, renowned, diverse array, breathtaking, stunning, must-visit. Say what the thing is.
17. **Borrowed authority.** experts argue, observers have cited, industry reports, some critics; lists of outlets; follower counts. Use the named source and what it said, or cut. Never invent a source.
18. **Avoiding is/are/has.** serves as, stands as, functions as, represents, boasts, features, offers. Use is, are, has.

## D. Formatting by rule

19. **Bold as decoration.** Bold without reason; lists where every item has a bold label and colon. Remove bold; turn labeled lists into prose when labels add nothing.
20. **Decorative headings.** Title Case headings, emojis or arrows as decoration, a rule between every section, a top heading repeating the title. Sentence case, no decoration.
21. **Curly quotes** where the format uses straight ones. *Weak alone.*

## E. Leftovers (remove outright)

22. **Chatbot residue.** I hope this helps, Of course!, Great question!, You're absolutely right, Would you like..., Want me to...?, let me know, here is a... Keep the content, drop the wrapper.
23. **Knowledge-limit disclaimers and guesses.** as of my last update, based on available information, not publicly documented, maintains a low profile, likely grew up, it is believed. State what the source does not show or cut; never present a guess as fact.
24. **Heading repeated in the first sentence.** Remove the restating line.
25. **Writing about the previous version** in docs or comments. Describe current behavior; history belongs in changelogs and migration guides.

## F. Added from stop-slop (Hardik Pandya, MIT)

26. **False agency.** An inanimate thing performs a human verb: "the complaint becomes a fix", "the decision emerges", "the data tells us", "the market rewards". Name who acts. Keep it in code and system descriptions where the system is the actor ("the server retries").
27. **Lazy extremes.** every, always, never, everyone, no one, all used for emphasis rather than as a checked claim. Give the real scope ("most of the 30 contracts") or cut. Keep them when literally true.
28. **Narrator from a distance.** "People often find", "Nobody designed this", "It is worth noting". Address the reader ("you") or name who did it. Reference and legal text may stay impersonal.
29. **Meta-joiners and Wh- setups.** "The rest of this essay", "As we will see", "What this means is", "Why does this matter? Because". Delete the joiner; start with the point.
30. **Empty adverbs.** really, truly, simply, deeply, incredibly, extremely, fundamentally. Cut them; keep adverbs that change meaning (only, never, already, approximately). *Weak alone.*
