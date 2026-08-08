"""Analyst specs for the broad universe (batch 2). Same rules as always:
every number in claims traces to an accession; scenario EPS/multiples/fair
values are labeled judgment; data defects are stated, never papered over.

KLAC is deliberately ABSENT: its market cap fails share-count/price
reconciliation post-split, so the machine refuses a report until fixed."""


def S(summary, direction, strength, drivers, deters, fdrivers, scenarios,
      thesis, wrong, priced, catalysts, risks, invalid, bear, fragile,
      acct, valc, unresolved, classification, conviction, rr, claims):
    return {
        "business_assessment": {"summary": summary},
        "fundamental_trend": {"direction": direction, "strength": strength,
                              "primary_drivers": drivers,
                              "primary_deteriorations": deters},
        "forecast_drivers": fdrivers,
        "scenarios": scenarios,
        "investment_thesis": {"summary": thesis,
                              "why_market_may_be_wrong": wrong,
                              "what_is_already_priced_in": priced,
                              "catalysts": catalysts, "risks": risks,
                              "invalidation_conditions": invalid},
        "adversarial_review": {"strongest_bear_case": bear,
                               "fragile_assumptions": fragile,
                               "accounting_concerns": acct,
                               "valuation_concerns": valc,
                               "unresolved_questions": unresolved},
        "conclusion": {"classification": classification,
                       "conviction": conviction, "risk_reward_score": rr},
        "claims": claims,
    }


def C(claim, cls, *src):
    return {"claim": claim, "classification": cls, "source_ids": list(src)}


def sc(bear, base, bull, pb=0.25, p0=0.50, pu=0.25):
    out = []
    for name, prob, item in (("bear", pb, bear), ("base", p0, base),
                             ("bull", pu, bull)):
        if isinstance(item, tuple):
            out.append({"name": name, "probability": prob,
                        "eps": item[0], "valuation_multiple": item[1]})
        else:
            out.append({"name": name, "probability": prob,
                        "fair_value": item})
    return out


SPECS_EXTRA = {}

SPECS_EXTRA["GM"] = S(
    "Auto OEM with a 19.9% FCF yield and a price implying a -30%/yr cash-flow "
    "decline against +5.3% delivered revenue CAGR — among the deepest "
    "pessimism in the universe. Beats every quarter (last three: +11%, +42%, "
    "+12%). Captive-finance operations make net-debt framing unreliable.",
    "MIXED", "MODERATE",
    ["FCF $14.4B at P/FCF 5.0; relentless buybacks shrink the share count",
     "Earnings beats: expectations score 100 across the surprise window"],
    ["Operating income -31.4% YoY on EV losses and warranty charges",
     "Revenue growth near zero (+2.1% YoY)"],
    ["EV losses narrowing vs ICE cash cows persisting",
     "Buyback pace against a 5× FCF multiple"],
    sc(65.0, 95.0, 125.0),
    "The price requires deep perpetual decline while the company converts a "
    "fifth of its market cap to free cash annually and retires shares. The "
    "base case needs stability, not success. Sector-relative base rates for "
    "this profile: 60.5% outperformed, median +15.5% (n=historical panel).",
    ["Tariff/EV-transition fear has overshot the actual cash trajectory"],
    ["Terminal decline of the ICE business at a rate cash flow does not yet show"],
    ["Earnings 2026-10-20", "Continued buyback at ~20% FCF yield"],
    ["Cyclical peak: auto demand rollover would hit revenue and FCF together",
     "Captive-finance leverage is opaque in our normalized view (net cash figure misleading)"],
    ["FCF margin below 5%", "First guidance cut after the beat streak",
     "Auto credit losses turning in the finance book"],
    "GM is a melting ice cube priced as one: 1.1% operating margin, EV "
    "programs burning cash, and the 'cheap' FCF partly reflects deferred "
    "capex and finance-book accounting. Cyclical FCF yields are highest at "
    "the top.",
    ["Base case assumes flat volumes through a possible consumer-credit downcycle",
     "Net-cash position is an artifact of excluding captive-finance debt from our debt tags"],
    ["OCF/NI of 11.9 signals large non-cash charges distorting NI"],
    ["P/E 38.6 on charge-depressed EPS conflicts with P/FCF 5.0 — the FCF view is the informative one"],
    ["Cruise/autonomy spend trajectory (segment data pending)"],
    "ATTRACTIVE", "MEDIUM", 63,
    [C("TTM FCF is $14.4B against a $73B market cap (19.9% yield); price-implied growth is -30%/yr.",
       "FACT", "SEC:ACCESSION:0001467858-26-000051", "YAHOO:CHART:GM"),
     C("Operating income fell 31.4% YoY while the last three EPS surprises were +11.1%, +41.8%, +11.9%.",
       "FACT", "SEC:ACCESSION:0001467858-26-000035", "FMP:EARNINGS:GM"),
     C("The market is pricing terminal decline that current cash generation does not corroborate.",
       "INFERENCE", "SEC:ACCESSION:0001467858-26-000051"),
     C("Probability-weighted 12-month value is ~+15% above the current price.",
       "FORECAST")])

SPECS_EXTRA["F"] = S(
    "Auto OEM at a 17.8% FCF yield with the price implying a -36%/yr decline "
    "— deeper pessimism than GM. GAAP EPS is negative (-$1.50 TTM) on "
    "special items (non-operating flag auto-raised); FCF of $9.5B is the "
    "cleaner read. One director made a discretionary purchase; beats run "
    "hot but off distorted bases (+261% latest).",
    "MIXED", "WEAK",
    ["FCF $9.5B, P/FCF 5.6; dividend-paying with net cash on our tags",
     "Revenue +6.4% YoY — demand holding"],
    ["Negative GAAP EPS on charges; operating margin -3.8%",
     "Quality gap: OCF/NI negative (-3.1) — earnings and cash tell different stories"],
    ["Warranty/special-item normalization", "Model-e loss trajectory"],
    sc(10.0, 16.0, 22.0, 0.30, 0.45, 0.25),
    "Priced for faster decline than any delivered metric shows, with cash "
    "flow covering the market cap in ~5.6 years. But the earnings-vs-cash "
    "divergence and recurring 'special' charges make this lower quality "
    "than GM's version of the same setup.",
    ["Charges are treated as permanent by the market; if they are cyclical, normalized EPS re-rates the stock"],
    ["Perpetual value destruction in EVs and warranty costs"],
    ["Earnings 2026-07-28 (3 days)", "Any quarter of positive GAAP operating income"],
    ["Charge recurrence: 'one-time' warranty costs have recurred for years",
     "Captive-finance credit exposure late-cycle"],
    ["FCF below $6B TTM", "Dividend cut", "Another >$1B warranty charge"],
    "Ford is the value trap the accruals literature warns about: persistent "
    "negative accrual gaps, negative GAAP earnings, and FCF propped by "
    "working-capital timing. The -36%/yr implied decline may simply be the "
    "market correctly pricing charge recurrence.",
    ["Base case assumes charges normalize — they have not for three years",
     "Fair values are FCF-multiple judgments on a cyclical peak base"],
    ["OCF/NI is negative; NONOP flag active — GAAP EPS unusable"],
    ["The 17.8% FCF yield is the market saying it does not believe the F in FCF"],
    ["Ford Credit book quality (not parsed)"],
    "WATCH", "LOW", 50,
    [C("TTM FCF is $9.5B against a $54B market cap; GAAP diluted EPS is -$1.50 on special items.",
       "FACT", "SEC:ACCESSION:0000037996-26-000086"),
     C("Price-implied growth is -36%/yr, the most pessimistic in the coverage universe.",
       "FACT", "YAHOO:CHART:F", "SEC:ACCESSION:0000037996-26-000086"),
     C("Cash flow and reported earnings diverge materially (OCF/NI -3.1) — earnings quality is low.",
       "INFERENCE", "SEC:ACCESSION:0000037996-26-000086"),
     C("Probability-weighted value is modestly above price with a wide, low-conviction distribution.",
       "FORECAST")])

SPECS_EXTRA["BKNG"] = S(
    "Travel marketplace compounding revenue at 16.4% (3-yr CAGR) with the "
    "price now implying just +2.1%/yr — a -11.6pt expectations gap after a "
    "20.6% one-year decline. 32.6% operating margin, $9.0B FCF. Note: "
    "consensus/guidance data plan-gated; per-share figures reflect the 2026 "
    "share split.",
    "IMPROVING", "MODERATE",
    ["Revenue +16.2% YoY with operating income +19.7% — leverage intact",
     "FCF yield 6.6% with modest net debt ($2.4B)"],
    ["Old-era balance-sheet reconciliation gaps in 2009-2012 periods (data note, not current)"],
    ["Room-night growth vs tough travel comps", "AI-agent booking disintermediation risk"],
    sc((7.0, 18.0), (9.0, 22.0), (11.0, 26.0)),
    "A 16%-growth marketplace priced for 2% growth at 15× FCF. The AI-"
    "disintermediation fear (agents booking direct) explains the de-rating; "
    "the countercase is that aggregated supply and loyalty economics have "
    "survived every prior disintermediation scare.",
    ["Agent-booking fear repeats the Google-kills-OTAs thesis that failed for a decade"],
    ["Growth fading to GDP-rate immediately"],
    ["Quarterly room-night and bookings prints", "Buyback capacity at depressed multiple"],
    ["AI agents could genuinely commoditize aggregation this time",
     "European regulatory pressure on ranking practices"],
    ["Revenue growth below 8% for two quarters", "Take-rate compression >50bps"],
    "If AI assistants book travel directly with suppliers, BKNG's ad-spend "
    "moat inverts into a cost disadvantage, and 22× earnings on peak travel "
    "demand is expensive. The 100th-percentile P/E-vs-history is regime-"
    "adjusted rich, not cheap.",
    ["Base case assumes take-rates hold through channel shift",
     "No consensus visibility on this plan tier"],
    ["None material; old-period reconciliation gaps are era artifacts"],
    ["P/E percentile at 100 vs own history cuts against the cheap-vs-growth framing"],
    ["Mix of agency vs merchant bookings (segment parsing pending)"],
    "ATTRACTIVE", "MEDIUM", 62,
    [C("TTM revenue grew 16.2% YoY with a 32.6% operating margin and $9.0B FCF.",
       "FACT", "SEC:ACCESSION:0001075531-26-000025"),
     C("The price implies +2.1%/yr growth vs a 16.4% delivered 3-year CAGR — a -11.6pt gap.",
       "FACT", "YAHOO:CHART:BKNG", "SEC:ACCESSION:0001075531-26-000025"),
     C("The de-rating prices AI-agent disintermediation of travel aggregation.",
       "INFERENCE", "YAHOO:CHART:BKNG"),
     C("Probability-weighted 12-month value is ~+14% above the current price.",
       "FORECAST")])

SPECS_EXTRA["IBM"] = S(
    "Enterprise software/consulting hybrid: slow growth (+1.1% YoY) but "
    "6.8% FCF yield, and TWO insiders made discretionary open-market "
    "purchases with zero sales after a 15.4% one-year decline. Operating "
    "margin untagged in XBRL (data note); $49B net debt is the structural "
    "constraint.",
    "STABLE", "WEAK",
    ["FCF $13.8B (20.0% margin), P/FCF 14.6",
     "Two-insider discretionary buying cluster — rare among mega-caps"],
    ["Revenue growth ~1% — the growth pivot keeps not arriving",
     "$49B net debt limits optionality"],
    ["Software mix shift and AI-consulting bookings", "Debt paydown pace"],
    sc((10.0, 14.0), (11.5, 18.0), (13.0, 22.0)),
    "A bond-like cash streamer at a fair price with insiders signaling "
    "value. The expectations bar is low (+5.8%/yr implied vs +3.7% "
    "delivered) but not depressed; this is income-with-optionality, not a "
    "mispricing.",
    ["AI consulting could re-accelerate bookings beyond the priced ~6%"],
    ["Low-single-digit growth forever"],
    ["Bookings disclosure in upcoming earnings", "Insider cluster continuation"],
    ["Consulting cyclicality in a downturn", "Debt service if rates stay high"],
    ["FCF below $11B TTM", "Software revenue declining YoY"],
    "IBM has re-rated on an AI narrative twice before and round-tripped "
    "both times; 1% growth with $49B of debt is a leveraged bond, and the "
    "insider purchases are small relative to executive net worth.",
    ["Base case multiple (18×) assumes the software mix story holds",
     "Operating margin unavailable from XBRL — margin structure unverified"],
    ["Missing operating-income tag — subtotal structure not independently verifiable"],
    ["P/E 19 for 1% growth is only cheap against the insiders' apparent view"],
    ["Consulting vs software segment split (pending)"],
    "WATCH", "MEDIUM", 55,
    [C("TTM FCF is $13.8B (6.8% yield); revenue grew 1.1% YoY; net debt is $49.0B.",
       "FACT", "SEC:ACCESSION:0000051143-26-000078"),
     C("Two insiders made discretionary open-market purchases in the trailing 6 months with zero discretionary sales.",
       "FACT", "SEC:FORM4:IBM"),
     C("The setup is fair-value income with insider-signaled optionality, not a statistical mispricing.",
       "INFERENCE", "YAHOO:CHART:IBM", "SEC:ACCESSION:0000051143-26-000078"),
     C("Probability-weighted 12-month value approximates the current price.",
       "FORECAST")])

SPECS_EXTRA["INTU"] = S(
    "Tax/accounting software monopoly down 61.9% in a year on AI-"
    "disruption fear — the largest de-rating in the coverage set. Price "
    "implies -6.3%/yr vs +14.0% delivered CAGR (a -24pt gap); 9.6% FCF "
    "yield; one insider bought twice. SBC at 9.7% of revenue is the "
    "quality offset.",
    "STABLE", "MODERATE",
    ["Revenue +10.4% YoY, 3-yr CAGR +14.0% — no filed deceleration matching the price action",
     "FCF margin 37.1%; P/FCF 10.5"],
    ["Operating income growth (+8.1%) trailing revenue",
     "SBC 9.7% of revenue dilutes per-share economics"],
    ["Whether AI does tax returns (disruption) or does them inside TurboTax (moat deepening)",
     "Credit Karma / SMB cross-cycle performance"],
    sc((14.5, 13.0), (18.0, 18.0), (21.0, 23.0), 0.30, 0.50, 0.20),
    "The market has moved from pricing Intuit as an AI winner to an AI "
    "casualty in twelve months without a matching change in filed results. "
    "At 10.5× FCF, the price embeds decline; the countercase is regulatory "
    "moat (tax complexity) plus distribution. Insider buying supports "
    "stabilization.",
    ["Tax-filing AI risk is overstated: compliance liability keeps consumers inside warranted software"],
    ["Mid-single-digit perpetual decline in cash flow"],
    ["FY-end earnings with TurboTax AI attach metrics", "Any large buyback authorization"],
    ["Free/AI-native tax filing gaining share at the low end is measurable and real",
     "SBC persistently near 10% of revenue"],
    ["Revenue growth below 7% for two quarters", "Consumer segment units declining",
     "SBC above 12% of revenue"],
    "Disrupted-monopoly pricing can be right: TurboTax's moat is partly "
    "regulatory friction that both parties have promised to remove, and "
    "Credit Karma is cyclical. A -62% year often precedes estimate cuts, "
    "not recoveries — catching this knife requires the filings to keep "
    "disproving the fear each quarter.",
    ["Base case assumes 18× on stable EPS — a multiple the market currently refuses",
     "No consensus data on this plan tier to verify the revision trend"],
    ["SBC 9.7% of revenue; gross margin untagged in latest normalization"],
    ["P/E percentile 0 vs own history is sector-regime-confounded"],
    ["Segment mix (Consumer vs SMB vs Credit Karma) pending"],
    "WATCH", "MEDIUM", 58,
    [C("The stock fell 61.9% in twelve months while TTM revenue grew 10.4% with a 37.1% FCF margin.",
       "FACT", "SEC:ACCESSION:0000896878-26-000025", "YAHOO:CHART:INTU"),
     C("Price-implied growth is -6.3%/yr vs +14.0% delivered — a -24.3pt expectations gap.",
       "FACT", "YAHOO:CHART:INTU", "SEC:ACCESSION:0000896878-26-000025"),
     C("One insider made two discretionary purchases in the trailing window; none sold.",
       "FACT", "SEC:FORM4:INTU"),
     C("Probability-weighted 12-month value is modestly above the current price.",
       "FORECAST")])

SPECS_EXTRA["NOW"] = S(
    "Enterprise workflow SaaS still compounding at +24.0% YoY (22.4% 3-yr "
    "CAGR) but down 49.0% in a year; price now implies 11.4%/yr — half the "
    "delivered rate (-16.8pt gap). The offsets are serious: SBC at 14.8% "
    "of revenue and operating income down 54.8% YoY on an investment "
    "cycle. One insider bought twice.",
    "MIXED", "MODERATE",
    ["Revenue +24.0% with 74.8% gross margin — franchise growth intact",
     "FCF margin 31.1%; P/FCF 22.3"],
    ["Operating income -54.8% YoY — spending through the AI transition",
     "SBC 14.8% of revenue, highest in the coverage set"],
    ["Agentic-AI product monetization vs seat cannibalization",
     "GAAP margin recovery from the investment trough"],
    sc(60.0, 110.0, 180.0, 0.30, 0.50, 0.20),
    "Halved growth expectations for a still-24%-grower is the widest "
    "software expectations gap after INTU; the question is whether GAAP "
    "economics ever emerge from under 15% SBC. FCF-based scenarios put "
    "base value ~11% above price.",
    ["Seat fear ignores that NOW sells workflows, not seats, and agents are workflow multipliers"],
    ["Growth halving immediately and SBC never normalizing"],
    ["cRPO growth at next print", "First quarter of GAAP operating-margin re-expansion"],
    ["SBC of this magnitude means per-share value grows far slower than the company",
     "Enterprise IT budget cyclicality"],
    ["Revenue growth below 18%", "SBC above 16% of revenue", "cRPO growth below revenue growth"],
    "On owner-earnings (FCF minus SBC), the multiple is ~2× the headline "
    "P/FCF and the 'cheap growth' vanishes. The -55% OI print shows what "
    "happens to GAAP economics when growth spending meets decelerating "
    "top-line — this can be a multi-year derating, not a dip.",
    ["Base fair value assumes 22× FCF without SBC adjustment",
     "No consensus coverage on this plan tier"],
    ["SBC 14.8% of revenue is the dominant quality issue"],
    ["Owner-earnings multiple roughly double the headline P/FCF"],
    ["Agentic product revenue disclosure (pending transcripts)"],
    "WATCH", "LOW", 54,
    [C("TTM revenue grew 24.0% YoY while operating income fell 54.8%; SBC is 14.8% of revenue.",
       "FACT", "SEC:ACCESSION:0001373715-26-000076"),
     C("Price-implied growth (11.4%/yr) is roughly half the delivered 3-year CAGR (22.4%).",
       "FACT", "YAHOO:CHART:NOW", "SEC:ACCESSION:0001373715-26-000076"),
     C("SBC-adjusted owner earnings roughly double the effective valuation multiple.",
       "INFERENCE", "SEC:ACCESSION:0001373715-26-000076"),
     C("Probability-weighted 12-month value is ~+10% above the current price with wide dispersion.",
       "FORECAST")])

SPECS_EXTRA["NFLX"] = S(
    "Streaming platform down 40.6% in a year (post-split prices) despite "
    "+13.4% revenue growth and a 29.7% operating margin. Price implies "
    "14.9%/yr — slightly ABOVE the 12.6% delivered, so this is a fallen "
    "growth premium, not a depressed-expectations entry. Beats continue "
    "(+1.5%, +61%, +1.3%).",
    "STABLE", "MODERATE",
    ["Revenue +13.4% YoY with operating margin 29.7% — model is executing",
     "ROIC 36.2%; consensus next-FY +5.5% looks conservative vs delivered"],
    ["FCF conversion softened (OCF/NI 0.88) on content-spend timing",
     "12m price action implies the market fears sub saturation"],
    ["Ad-tier monetization ramp", "Content-spend cycle vs FCF"],
    sc((2.9, 16.0), (3.7, 22.0), (4.4, 28.0)),
    "The 40% drawdown restored a reasonable entry to a franchise still "
    "compounding teens with best-in-class engagement — but the price still "
    "asks for ~15%/yr, so the margin of safety is execution, not "
    "expectations. Attractive on quality-at-fair-price grounds.",
    ["Saturation fear underweights advertising ARPU as a second S-curve"],
    ["Continued mid-teens compounding — this is NOT a low-expectations setup"],
    ["Earnings 2026-10-20; ad-tier disclosure", "Sports-rights announcements as engagement catalysts"],
    ["Content-cost inflation cycles returning", "Password-sharing tailwind exhausted"],
    ["Revenue growth below 9%", "Operating margin below 25%", "FCF negative for two quarters"],
    "Everyone's streaming bear case eventually happened to someone: "
    "engagement is a fashion asset, the ad tier cannibalizes premium ARPU, "
    "and at 22× earnings the stock re-rates violently on any sub miss — "
    "as the -40% year just demonstrated.",
    ["Base assumes 22× holds through a saturation scare",
     "OCF softness assumed timing, not structural"],
    ["OCF/NI 0.88 worth monitoring against content amortization policy"],
    ["P/E 5-yr percentile 90 — still rich vs own history"],
    ["Regional sub economics (segment parsing pending)"],
    "ATTRACTIVE", "MEDIUM", 61,
    [C("TTM revenue grew 13.4% with a 29.7% operating margin and 36.2% ROIC.",
       "FACT", "SEC:ACCESSION:0001065280-26-000212"),
     C("The stock fell 40.6% over twelve months; price-implied growth (14.9%/yr) still exceeds delivered (12.6%).",
       "FACT", "YAHOO:CHART:NFLX"),
     C("This is a quality-at-fair-price entry, not a depressed-expectations one.",
       "INFERENCE", "YAHOO:CHART:NFLX", "SEC:ACCESSION:0001065280-26-000212"),
     C("Probability-weighted 12-month value is ~+19% above the current price.",
       "FORECAST")])

SPECS_EXTRA["ORCL"] = S(
    "Database incumbent betting the company on AI infrastructure: revenue "
    "+20.6% YoY but TTM FCF is NEGATIVE $23.7B on a debt-funded datacenter "
    "buildout; the stock halved (-52.6%) as the market moved from pricing "
    "the option to pricing the risk. Reverse DCF (NOPAT basis) implies "
    "~4.9%/yr — modest, IF the capex eventually earns its cost of capital.",
    "MIXED", "STRONG",
    ["Revenue +20.6% YoY, operating income +20.1% — the core business funds the bet",
     "Backlog-driven growth visibility (RPO) is the bull's evidence"],
    ["FCF -$23.7B TTM; the buildout is entirely debt-and-cash-flow financed",
     "Net position swung deeply negative excluding operating leases"],
    ["AI-capacity utilization as it comes online", "Debt-market conditions for continued funding"],
    sc((5.0, 14.0), (6.5, 19.0), (8.0, 25.0), 0.30, 0.50, 0.20),
    "A 20%-growing software company at 19.6× earnings is reasonable; a "
    "company burning $24B/yr on GPU datacenters ahead of contracted "
    "utilization is a leveraged bet. The halved price now embeds modest "
    "expectations, making the risk/reward roughly balanced with fat tails "
    "in both directions.",
    ["Contracted AI backlog may make the capex safer than the cash statement looks"],
    ["Meaningful probability the AI buildout destroys capital"],
    ["Quarterly RPO/backlog prints", "First quarter of positive FCF inflection"],
    ["If AI training demand pauses, ORCL holds depreciating GPUs bought with debt",
     "Customer concentration in a handful of AI labs"],
    ["FCF worse than -$30B TTM", "RPO growth below 30%", "Debt spread widening >150bps"],
    "Negative $24B FCF is not an investment phase — it is the single "
    "largest cash burn in the coverage universe, funded by debt, for "
    "capacity whose pricing is set by customers with their own chips. The "
    "-53% year is the market correctly repricing counterparty and "
    "obsolescence risk.",
    ["NOPAT-basis reverse DCF flatters the picture by ignoring the burn",
     "Scenario multiples assume the debt market stays open"],
    ["Capex capitalization pace vs depreciation reality worth auditing"],
    ["P/E is meaningless mid-buildout; EV/revenue 4.6 is the anchor"],
    ["Utilization/contract disclosure (transcripts plan-gated)"],
    "WATCH", "LOW", 50,
    [C("TTM revenue grew 20.6% while free cash flow was NEGATIVE $23.7B on the AI-infrastructure buildout.",
       "FACT", "SEC:ACCESSION:0001193125-26-277521"),
     C("The stock declined 52.6% over twelve months.",
       "FACT", "YAHOO:CHART:ORCL"),
     C("The market has repriced the AI buildout from option value to funding risk.",
       "INFERENCE", "YAHOO:CHART:ORCL", "SEC:ACCESSION:0001193125-26-277521"),
     C("Probability-weighted 12-month value is ~+7% above price with fat tails both ways.",
       "FORECAST")])

SPECS_EXTRA["PLTR"] = S(
    "Defense/enterprise AI software growing 84.7% YoY — the fastest in the "
    "universe — at 139× earnings and 59× EV/revenue. Even after a -22.6% "
    "year, price-implied growth (57%/yr for five years) exceeds the "
    "delivered 32.9% CAGR. Insiders sold $196M. Consensus expects +47.8% "
    "next FY.",
    "IMPROVING", "STRONG",
    ["Revenue +84.7% YoY with operating income +328% — hypergrowth WITH leverage",
     "FCF margin 51.5%; beats of +25%, +9%, +19%"],
    ["SBC 14.0% of revenue", "Insider selling $196M in the window"],
    ["US government + commercial AI contract velocity", "Whether 50%+ growth persists into the law of large numbers"],
    sc((0.9, 60.0), (1.3, 80.0), (1.9, 110.0), 0.30, 0.45, 0.25),
    "The rare case where hypergrowth is real and profitable — and still "
    "probably overpriced: sustaining the priced-in 57%/yr for five years "
    "would make PLTR one of the largest companies on earth. The scenario "
    "set's probability-weighted value sits below price even granting "
    "generous multiples.",
    ["If AI-platform consolidation makes PLTR the enterprise standard, the priced growth is achievable"],
    ["Five years of ~57% compounded growth"],
    ["Earnings 2026-08-03", "US government budget cycles"],
    ["Growth deceleration to merely 40% would compress the multiple violently",
     "Insider selling at scale during the de-rating"],
    ["Revenue growth below 40%", "Commercial segment deceleration two quarters running",
     "SBC above 16% of revenue"],
    "At 59× revenue, PLTR discounts not success but ubiquity. Every "
    "software company that traded here — regardless of execution — "
    "delivered sub-market returns over the following five years. The "
    "insiders selling $196M into a falling price have the same information "
    "the bulls do.",
    ["Bull case requires 110× earnings to persist at decade-scale",
     "Base-rate evidence is unavailable for this profile (insufficient analogs)"],
    ["SBC 14.0% of revenue"],
    ["EV/revenue 59 is 5-10× the software-sector norm for this margin structure"],
    ["Government vs commercial mix durability (segment pending)"],
    "UNATTRACTIVE", "LOW", 35,
    [C("TTM revenue grew 84.7% YoY with a 51.5% FCF margin — the fastest, most profitable growth in the universe.",
       "FACT", "SEC:ACCESSION:0001321655-26-000028"),
     C("The shares trade at 139× TTM earnings and 59× EV/revenue; insiders sold $196M in six months.",
       "FACT", "YAHOO:CHART:PLTR", "SEC:FORM4:PLTR"),
     C("Price-implied growth (57%/yr) exceeds even this company's delivered 32.9% CAGR.",
       "FACT", "YAHOO:CHART:PLTR", "SEC:ACCESSION:0001321655-26-000028"),
     C("Probability-weighted 12-month value sits below the current price.",
       "FORECAST")])

SPECS_EXTRA["ABNB"] = S(
    "Travel marketplace: +17.9% revenue growth, 82.9% gross margin, "
    "operating income +126% YoY. Two data gaps stated plainly: cash-flow "
    "statement fields are missing from the latest normalization (FCF "
    "unavailable — gate keeps completeness honest) and consensus is "
    "plan-gated. Price implies 21.9%/yr vs 13.4% delivered: expectations "
    "run AHEAD of history.",
    "IMPROVING", "MODERATE",
    ["Operating income +126% YoY on +17.9% revenue",
     "Net cash $9.5B; asset-light model"],
    ["FCF fields missing in current normalization — quality checks limited",
     "SBC 13.0% of revenue"],
    ["Experiences/services attach beyond core stays", "Regulatory outcomes in key cities"],
    sc((3.8, 22.0), (4.8, 28.0), (5.8, 34.0)),
    "A good business at a price that already assumes acceleration (implied "
    "21.9% > delivered 13.4%). Without the FCF series or consensus data, "
    "the machine cannot verify the cash story or the revision trend — "
    "conviction capped accordingly.",
    ["Services expansion could genuinely lift growth above trend"],
    ["Growth acceleration above anything yet delivered"],
    ["Next earnings (date unavailable on this plan)", "City-level regulatory rulings"],
    ["Travel cyclicality on discretionary spend", "Regulatory supply removal in major markets"],
    ["Revenue growth below 12%", "Nights-and-experiences growth decelerating two quarters"],
    "Paying 34× earnings for a travel cyclical at what may be peak leisure "
    "demand, while insiders show zero open-market buying, is momentum "
    "dressed as quality. The missing cash-flow data means the bull case "
    "rests on an income statement alone.",
    ["Scenario EPS extrapolates operating-income growth without cash-flow verification",
     "No consensus or FCF data to check against"],
    ["Cash-flow statement fields absent from the latest quarter's normalization — under investigation"],
    ["P/E 34 with expectations already ahead of delivered growth"],
    ["Cash-flow tag mapping for ABNB (data fix pending)"],
    "WATCH", "LOW", 48,
    [C("TTM revenue grew 17.9% YoY with operating income up 126%; net cash is $9.5B.",
       "FACT", "SEC:ACCESSION:0001559720-26-000014"),
     C("Price-implied growth (21.9%/yr) exceeds the delivered 3-year CAGR (13.4%).",
       "FACT", "YAHOO:CHART:ABNB"),
     C("Cash-flow fields are missing from the current normalization; the cash story is unverified.",
       "FACT", "SEC:ACCESSION:0001559720-26-000014"),
     C("Probability-weighted 12-month value approximates the current price.",
       "FORECAST")])

SPECS_EXTRA["CMG"] = S(
    "Fast-casual operator (post-split prices): unit economics intact (58.4% "
    "ROIC, 15.3% operating margin) but comparable momentum broke — "
    "operating income -17.2% YoY on +7.4% revenue, and the stock fell "
    "32.0%. Price implies 15.0%/yr vs 11.4% delivered: still a premium. "
    "Base rates for its bucket are negative (32.5% outperformed, median "
    "-9.1%).",
    "DETERIORATING", "MODERATE",
    ["ROIC 58.4% — the unit model remains elite",
     "Net cash; expansion self-funded"],
    ["Operating income -17.2% YoY — traffic/cost squeeze",
     "Historical base rates for this setup are unfavorable"],
    ["Traffic recovery vs price-driven comps", "New-unit returns at scale"],
    sc((1.0, 20.0), (1.25, 26.0), (1.5, 32.0), 0.30, 0.50, 0.20),
    "Great company, wrong price, weakening momentum: the -17% OI print "
    "against a still-premium multiple leaves no expectations cushion, and "
    "the machine's own base rates say this profile (mid-growth, low-yield, "
    "high-ROIC) UNDERperformed historically.",
    ["A quick traffic recovery would restore the compounding narrative fast"],
    ["A return to mid-teens earnings growth"],
    ["Quarterly comps and traffic prints", "Unit-growth guidance"],
    ["Margin structure vulnerable to beef/labor inflation", "Fast-casual competition intensifying"],
    ["Two more quarters of negative OI growth", "Comp traffic negative >2 quarters"],
    "When a premium-multiple compounder's operating income declines, the "
    "multiple usually follows with a lag — the -32% year may be the "
    "beginning of that adjustment, not the end. Neither insiders nor base "
    "rates argue otherwise.",
    ["Base case assumes margin recovery within four quarters",
     "Split-adjusted per-share continuity relies on the adjusted price series"],
    ["None material"],
    ["Premium multiple with deteriorating momentum and negative sector-relative base rates"],
    ["Traffic vs price mix (KPI parsing pending)"],
    "UNATTRACTIVE", "MEDIUM", 40,
    [C("Operating income fell 17.2% YoY on +7.4% revenue; ROIC remains 58.4%.",
       "FACT", "SEC:ACCESSION:0001058090-26-000028"),
     C("Historical setups in this stock's factor bucket outperformed only 32.5% of the time (median excess -9.1%).",
       "FACT", "SM:BACKTEST:PANEL"),
     C("The premium multiple lacks an expectations cushion against broken momentum.",
       "INFERENCE", "YAHOO:CHART:CMG", "SEC:ACCESSION:0001058090-26-000028"),
     C("Probability-weighted 12-month value approximates the current price at best.",
       "FORECAST")])

SPECS_EXTRA["COST"] = S(
    "The best retailer in the world at one of the most expensive prices in "
    "the coverage set: 47× earnings for +6.6% delivered 3-year CAGR, with "
    "the reverse DCF requiring 29.2%/yr — a 22.6pt premium over anything "
    "the business has ever done. Membership economics are impeccable; the "
    "price is the entire problem.",
    "STABLE", "MODERATE",
    ["Revenue +11.6% YoY; membership renewal economics best-in-class",
     "Net cash $14.3B; beats small and steady"],
    ["Latest surprise slightly negative (-1.4%)",
     "FCF margin 3.0% — thin retail economics regardless of quality"],
    ["Membership-fee increase cycle", "Grocery share gains vs price investment"],
    sc((18.0, 28.0), (21.0, 34.0), (23.0, 42.0), 0.30, 0.50, 0.20),
    "Nothing in Costco's operations justifies a sell — and nothing in its "
    "history justifies the price. Paying 47× for 7-11% growth requires the "
    "multiple to never normalize; every scenario with a sub-35× exit "
    "produces negative returns.",
    ["Membership pricing power could add an untapped earnings layer"],
    ["Perfection, indefinitely: ~29%/yr implied vs ~7% delivered"],
    ["Earnings 2026-09-24", "Membership fee increase announcement"],
    ["Multiple normalization is the entire risk — no operational failure needed",
     "Consumer trade-down cuts both ways at premium ticket sizes"],
    ["Comp growth below 4%", "Membership renewal below 90%", "Multiple below 30× (thesis-neutral trigger)"],
    "This is the cleanest pure-valuation short case in the universe: an "
    "unimpeachable business at a price requiring 4× its demonstrated "
    "growth rate. The quality is real; so was Nifty-Fifty quality in 1972.",
    ["Even the bear multiple (28×) is generous by historical retail standards",
     "Consensus next-FY growth (+2.9%) is BELOW the bear scenario's needs"],
    ["None — earnings quality is exemplary (OCF/NI 1.70)"],
    ["47× earnings, 55th percentile of own history, for single-digit delivered growth"],
    ["None material"],
    "UNATTRACTIVE", "MEDIUM", 35,
    [C("The shares trade at 47× TTM earnings while delivered 3-year revenue CAGR is 6.6%.",
       "FACT", "YAHOO:CHART:COST", "SEC:ACCESSION:0000909832-26-000051"),
     C("Price-implied growth is 29.2%/yr — 22.6 points above delivered history.",
       "FACT", "YAHOO:CHART:COST"),
     C("The investment case fails on valuation alone; operations are excellent.",
       "INFERENCE", "SEC:ACCESSION:0000909832-26-000051"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

SPECS_EXTRA["WMT"] = S(
    "Dominant retailer executing well (+7.1% revenue) at a price that has "
    "run far ahead: 38.5× earnings, 1.4% FCF yield, and a reverse DCF "
    "requiring 42.3%/yr — the largest implied-vs-delivered gap in the "
    "coverage universe (+34.8pt). Insiders (incl. family holding entities) "
    "sold $793M in the window.",
    "STABLE", "MODERATE",
    ["Revenue +7.1% YoY with advertising/membership margin layers scaling",
     "Beat cadence steady (+3.2%, +1.8%, +0.2%)"],
    ["FCF margin 1.75% — the AI/automation capex cycle absorbs the P&L gains",
     "Insider/holding-entity selling at scale"],
    ["Ad + marketplace margin mix vs core retail", "Capex normalization timing"],
    sc((2.6, 22.0), (3.1, 27.0), (3.5, 32.0), 0.30, 0.50, 0.20),
    "The e-commerce/ads transformation is real but fully — arguably more "
    "than fully — priced: 38.5× earnings for a 4-7% grower whose implied "
    "growth requirement (42%/yr) has no historical precedent at this "
    "scale. Execution risk is low; valuation risk is nearly all of it.",
    ["The ads/membership mix could lift margins faster than any retailer in history"],
    ["A margin transformation of unprecedented magnitude"],
    ["Earnings 2026-08-20", "Capex guidance inflection"],
    ["Multiple normalization to even 27× is a -30% move",
     "Grocery deflation compressing nominal comps"],
    ["Comp growth below 3%", "Ad revenue growth below 20%", "FCF margin below 1.5%"],
    "At a 1.4% FCF yield, Walmart is priced like a software monopoly while "
    "still being a 4%-operating-margin grocer. The $793M of insider and "
    "family sales into strength is consistent with the arithmetic: the "
    "price requires eight times the delivered growth rate.",
    ["Even the bull multiple (32×) requires believing in permanent re-rating",
     "Family-entity sales may be diversification, but their scale is unusual"],
    ["None material"],
    ["38.5× P/E at the 80th percentile of own history for mid-single-digit growth"],
    ["Ad-segment margin disclosure (pending)"],
    "UNATTRACTIVE", "MEDIUM", 34,
    [C("TTM revenue grew 7.1%; the shares trade at 38.5× earnings with a 1.44% FCF yield.",
       "FACT", "SEC:ACCESSION:0000104169-26-000102", "YAHOO:CHART:WMT"),
     C("Price-implied growth is 42.3%/yr vs 5.3% delivered — the widest gap in the universe.",
       "FACT", "YAHOO:CHART:WMT"),
     C("Insiders and family holding entities sold $793M in the trailing 6 months.",
       "FACT", "SEC:FORM4:WMT"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

SPECS_EXTRA["HD"] = S(
    "Home-improvement duopolist in a frozen housing market: +4.8% revenue, "
    "operating income -3.0%, and a price implying 11.8%/yr vs +1.5% "
    "delivered. A rate-cut housing recovery is the priced catalyst; until "
    "it arrives the stock discounts growth the P&L is not producing.",
    "STABLE", "WEAK",
    ["Pro-segment share gains; 4.3% FCF yield with modest leverage",
     "ROIC extraordinary (lease-adjusted artifacts aside)"],
    ["Operating income -3.0% YoY; comp-driven model lacks comps",
     "Receivables +7.8pt vs revenue (pro-credit expansion)"],
    ["Housing turnover recovery timing", "Pro vs DIY mix"],
    sc((12.5, 18.0), (14.5, 22.0), (16.0, 26.0), 0.30, 0.50, 0.20),
    "A fine franchise priced for a housing recovery that hasn't started: "
    "the +8.6pt expectations gap must close either by rates falling or the "
    "price falling. Neither timing is knowable; the yield pays little for "
    "waiting.",
    ["A genuine rate-cut cycle would validate the premium quickly"],
    ["A housing-turnover recovery within the forecast horizon"],
    ["Housing turnover data monthly", "Fed rate path"],
    ["A higher-for-longer decade keeps comps at zero",
     "Pro-credit receivables growth is a cycle-risk marker"],
    ["Comps negative two consecutive quarters", "Receivables gap above 15pt"],
    "HD without housing turnover is a zero-growth retailer at 24× — the "
    "market pays for the recovery every year and has been early for three. "
    "The receivables build to pro customers adds credit risk exactly where "
    "the cycle bites first.",
    ["Base case assumes recovery within 12 months",
     "ROIC figure inflated by lease accounting in our normalization"],
    ["Receivables growth outpacing revenue by 7.8pt"],
    ["Premium multiple with negative OI growth"],
    ["Pro-credit program exposure (not parsed)"],
    "WATCH", "MEDIUM", 48,
    [C("Revenue grew 4.8% YoY while operating income declined 3.0%.",
       "FACT", "SEC:ACCESSION:0001628280-26-038247"),
     C("Price-implied growth (11.8%/yr) vs delivered (+1.5%) leaves an +8.6pt expectations gap.",
       "FACT", "YAHOO:CHART:HD"),
     C("The stock is a housing-recovery option paying little while waiting.",
       "INFERENCE", "SEC:ACCESSION:0001628280-26-038247"),
     C("Probability-weighted 12-month value is ~7% below the current price.",
       "FORECAST")])

SPECS_EXTRA["LOW"] = S(
    "The cheaper half of the home-improvement duopoly: 17.5× earnings, "
    "6.5% FCF yield, price implying just 1.5%/yr vs -3.9% delivered — "
    "expectations nearly flat against a business whose revenue jumped "
    "+10.3% this year on acquisition + recovery. The better risk/reward "
    "of the pair.",
    "STABLE", "WEAK",
    ["Revenue +10.3% YoY; FCF yield 6.5% at 15.3× P/FCF",
     "P/E at the 25th percentile of own five-year history"],
    ["3-year CAGR still negative (-3.9%) — the base was shrinking",
     "Same housing-freeze exposure as HD"],
    ["Housing turnover; Pro-segment integration", "Buyback pace at depressed multiple"],
    sc((10.5, 14.0), (12.0, 17.5), (13.5, 21.0)),
    "Same macro option as HD at 25% less per unit of earnings and near-zero "
    "embedded expectations: the pair trade writes itself. Absolute upside "
    "still needs housing to thaw.",
    ["Acquisition integration could add share gains independent of macro"],
    ["Roughly nothing: implied growth is 1.5%/yr"],
    ["Housing turnover data", "Integration milestones"],
    ["DIY exposure is more cyclical than HD's pro mix", "Leverage higher than HD's"],
    ["Comps negative two quarters", "FCF yield below 5% via price appreciation without earnings"],
    "Lowe's discount to HD is structural (worse locations, weaker pro "
    "franchise), not a mispricing — and a -3.9% revenue CAGR business at "
    "any multiple is still shrinking. Cheapness alone was a losing factor "
    "in our own backtest.",
    ["Assumes the +10.3% print is a trend change, not an acquisition artifact"],
    ["None material"],
    ["25th percentile P/E is only meaningful if the shrinkage has ended"],
    ["Organic vs acquired growth split (pending)"],
    "WATCH", "MEDIUM", 56,
    [C("Revenue grew 10.3% YoY; the shares trade at 17.5× earnings with a 6.5% FCF yield.",
       "FACT", "SEC:ACCESSION:0000060667-26-000072"),
     C("Price-implied growth is 1.5%/yr — near-zero embedded expectations.",
       "FACT", "YAHOO:CHART:LOW"),
     C("The risk/reward is superior to HD's on identical macro exposure.",
       "INFERENCE", "YAHOO:CHART:LOW", "YAHOO:CHART:HD"),
     C("Probability-weighted 12-month value is ~+2% above the current price; the housing option is free-ish.",
       "FORECAST")])

SPECS_EXTRA["TGT"] = S(
    "Discount retailer mid-turnaround: +34.6% stock year on margin repair "
    "hopes, +6.7% revenue, but operating income -22.9% and a 3-year CAGR "
    "still negative. Beats have been large (+4.1%, +13.0%, +16.3%) as "
    "estimates overshot the downside. Price implies 8.7%/yr vs -1.3% "
    "delivered.",
    "MIXED", "WEAK",
    ["Consistent estimate beats — the expectations bar stayed too low",
     "FCF yield 4.9% at 18.1× earnings"],
    ["Operating income -22.9% YoY — the margin repair is not yet real",
     "Traffic-driven model still losing share to WMT/COST"],
    ["Merchandising reset traction", "Shrink and mix normalization"],
    sc((6.5, 12.0), (7.8, 15.0), (9.0, 18.0), 0.30, 0.50, 0.20),
    "The +35% recovery already prices the turnaround's success while "
    "operating income still falls — the easy expectations gap has closed. "
    "Remaining upside requires actual margin delivery, not just "
    "estimate-beating.",
    ["Margin structure could recover 200bps+ if shrink and mix normalize together"],
    ["A successful margin turnaround (~9%/yr growth implied)"],
    ["Earnings 2026-08-19", "Holiday-season execution"],
    ["Share-loss to scale discounters is structural",
     "Consumer trade-down hurts discretionary mix disproportionately"],
    ["OI decline continuing past two more quarters", "Comp traffic negative"],
    "Target's beats measure analyst pessimism, not business recovery: "
    "operating income fell 23% while the stock rose 35%. That combination "
    "— price recovering ahead of profits — is how turnaround premiums "
    "evaporate.",
    ["Base case assumes margin repair the current P&L contradicts"],
    ["OCF/NI 2.03 flags heavy non-cash adjustments"],
    ["Multiple re-rated ahead of any operating evidence"],
    ["Category mix and shrink quantification (pending)"],
    "WATCH", "LOW", 45,
    [C("Operating income fell 22.9% YoY while the stock returned +34.6%.",
       "FACT", "SEC:ACCESSION:0000027419-26-000022", "YAHOO:CHART:TGT"),
     C("The last three EPS surprises were +4.1%, +13.0%, +16.3% — estimates ran too pessimistic.",
       "FACT", "FMP:EARNINGS:TGT"),
     C("The recovery trade has consumed the expectations gap before the margins arrived.",
       "INFERENCE", "YAHOO:CHART:TGT"),
     C("Probability-weighted 12-month value is below the current price pending margin proof.",
       "FORECAST")])
