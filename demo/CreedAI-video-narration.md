# Creed A.I demo video: final narration

The exact narration of the submitted video (`CreedAI_Demo.mp4` in the team Drive folder, 9:20). Based on Aurin's `DEMO_VIDEO_SCRIPT.md`, with Arkadio as the assistant, the team credits, extra on-screen detail and the 16-week plan. Voice: Kokoro (local neural TTS). "Creed A.I" is spoken as "Creed ay-eye".

**0:00**

Hi, we're the Creed A.I team. Small business owners hear about A.I tools every day, but they don't have time to figure out which one actually fits a restaurant, a clinic or an HVAC shop.

**0:13**

Review sites tell you what people liked. They don't tell you whether a tool does your specific job, what it will really cost you each month, or whether you're already paying for something that covers it.

**0:24**

So we built Creed A.I. You tell it your business and the job you need done, and it shows tools that were actually checked for that job, with the price and the source. And when you want to talk it through, there's Arkadio, our assistant.

**0:38**

We're Arvind, who built Arkadio and its security guardrails, including testing against the OWASP Top 10 for A.I applications. Giri, who designed the interface and the sign-in experience. And Aurin, who built the core app and its architecture.

**0:54**

Let's walk through it, as a restaurant owner.

**0:58**

Here are some common situations. Each slide is a real kind of business with a real problem, like missed calls or staff scheduling. Nineteen kinds of business are covered today, from coffee shops and bakeries to plumbers, salons and clinics, along with twelve everyday jobs, like missed calls, invoicing, reviews and bookkeeping.

**1:18**

Let's be a restaurant owner. I type my business here. Now I pick a task, staff scheduling. Notice the business still says Restaurant. That's one of the bugs we fixed. I'll come back to it. You don't need to know any software names to start. Every field is optional, and if your problem isn't on the list, you can describe it in your own words.

**1:39**

The top tells me there's one recommended tool and three more options. It also tells me exactly which business and which task these results are for, so I always know what I'm looking at.

**1:50**

Recommended means one thing only. We checked this tool for this exact task, for this kind of business. You see the monthly price, and if you open details, every fact links to the vendor page it came from and the date we checked it. For a restaurant, that's Homebase, which has a free plan that covers staff scheduling.

**2:09**

Everything else goes under More options. Each card is simple: the name, a one-line summary, a link to the website, a button to compare and a heart to save it. We don't stamp these as good fits, because we haven't proven that yet. Here, that's 7shifts, Connecteam and When I Work: real alternatives you can compare, but not recommendations.

**2:31**

I can put up to three side by side. I choose the tasks I care about and the table shows which tool covers which task and the added monthly cost. If I already pay for a tool, it tells me so instead of making me buy it twice. And every number in that table comes from the same sourced facts, not from the A.I.

**2:50**

Here's a case where nothing has been checked yet for this task. Instead of a zero recommended message or a page full of question marks, you get Tools to look at: real tools in this area with a link to their site. Honest, and still useful.

**3:05**

Now, meet Arkadio.

**3:07**

This is Arkadio. You can talk to it the way you'd talk to a friend who knows software. Arkadio reads our catalog live through a tool call, not from memory. Its answer comes with the same cards you saw before, and the same rule applies. It only calls something recommended when we've checked it. You can start with a few quick questions about your business, or just type what's slowing you down, like, I keep missing calls while I'm on a job.

**3:32**

Here's the part we care most about. Arkadio cannot just make up a price or a feature. Before its answer reaches you, our server checks that it really called the catalog tool, that the result matches what we expected, and that every claim points to stored evidence. If any of that fails, you don't get a made up answer. You get a safe fallback. It also has guardrails on what goes in. If someone tries to talk it out of its rules, it politely refuses, like you see here. And if someone pastes a card number, a Social Security number, or a password, it's stopped before it ever reaches the model. Arvind tested Arkadio against every item in the OWASP Top 10 for A.I applications, with fifty-four automated security checks.

**4:16**

If you sign in, you can save plans and favourites, and your chat history comes back next time. That's tied to your account only. Nobody else can read it. Passwords need eight to fourteen characters and at least one number, and a forgotten password can be reset by email.

**4:33**

So how does it all work?

**4:35**

Here's how it's built. It runs on one Azure virtual machine in Belgium Central, as nine Docker Compose services. Caddy is the front door. It handles HTTPS and security headers. The FastAPI app holds the core logic: matching, comparison, plans and sign-in checks. The matching is deterministic. The model never decides what is recommended. Hermes is the agent runtime that runs Arkadio. The MCP executor is the only tool Arkadio can call. It's read-only and it gives public catalog details. Every call leaves a receipt. The model gateway meters every model request. It reserves the cost before the call and settles it after. That's how we keep a hard spending cap. The verification worker discovers new tools and checks vendor pages for prices and features. The renderer is a sandboxed Chromium for pages that need JavaScript. It can only reach the internet through a guarded proxy that blocks private addresses. Redis keeps chat history per account, and SQLite holds the catalog, plans, receipts and the spend ledgers.

**5:43**

Sign-in uses Supabase. We verify the actual token with Supabase on every request, we don't just decode it. The model is Claude Haiku 4.5 through OpenRouter.

**5:55**

This is the flow for one Arkadio answer. The app registers what it expects before Arkadio runs. Arkadio calls the tool, the tool writes a receipt with a hash of the result. When Arkadio finishes, the app checks that receipt and hash itself. A model saying success is never enough.

**6:13**

And the budget is real. We set lifetime limits: four dollars for chat, one dollar for discovery, ten dollars total for testing. The whole project so far has used about four and a half dollars.

**6:26**

We want to be honest about what broke, because finding these is what made the product trustworthy.

**6:32**

One. Tools looked recommended when they weren't. When we checked one tool for a task, like Homebase for restaurant scheduling, the page showed the tools next to it as Also a good fit, even though nobody had checked them. We ran an audit of two hundred and forty business and task combinations, and found twenty-five where this happened, including medical staff scheduling, which is exactly where a wrong answer matters. We fixed it at the data level. Each tool now carries which tasks it was checked for, and the page only says Recommended when that's true.

**7:05**

Two. The page was full of not sure labels. Cards used to say things like Needs checking, or Source not found. That's honest, but it's noise for a business owner. Now if we don't know a fact, we leave it off. The full record, with sources and dates, is still kept in the backend.

**7:23**

Three. The carousel changed your business. If you typed Restaurant and then clicked a shortcut on a coffee shop slide, your business quietly became Coffee shop, and the results changed. Now what you type is kept.

**7:37**

Four. Arkadio could describe features a tool never returned. In early tests, the model wrote nice sounding descriptions that weren't in our data. That's why we added the receipt and evidence check you saw in the architecture. Arkadio can only say what the catalog actually returned.

**7:56**

Five. Prices that were really guesses. Some vendor pages show annual or per-seat prices, or hide them behind JavaScript. We now only accept a monthly price when the plan, the price and the currency all come from the same official page. If it's unclear, we don't show a price at all.

**8:14**

And if we had sixteen weeks, here's the plan. Weeks one to four: depth. Grow the checked catalog from a few dozen tools to a few hundred, with automatic re-checks so prices never go stale. Weeks five to eight: real owners. Pilots with local shops, adding what they ask for, like sourced reviews and setup guides. Weeks nine to twelve: Arkadio does more. Connecting the tools an owner already uses, more languages, and an outside security review. And weeks thirteen to sixteen: launch. Ready for many users at once, a sustainable cost per conversation, and one number that matters: how much time and money we saved an owner.

**8:54**

So that's Creed A.I. You tell it your business and your problem. It shows you tools that were actually checked for that job, the real monthly cost and the source, and Arkadio helps you think it through without making things up. Next, we're adding sourced customer reviews and making long chats more resilient. Thanks for watching.
