"""Analyst specs batch 3: the AI-semis complex + remaining hardware/consumer.
The recurring pattern here is extreme positive expectations gaps — prices
requiring 25-70%/yr growth against low-single-digit delivered histories."""

from specs_extra import S, C, sc

SPECS_EXTRA2 = {}

SPECS_EXTRA2["AMD"] = S(
    "AI-accelerator #2 after a +213.5% year: revenue +37.9%, consensus "
    "expects +33.7% next FY, and the reverse DCF requires 52.0%/yr for "
    "five years — above even the AI-optimist consensus. Real business, "
    "extraordinary embedded expectations. Beats consistent (+2.6%, +15.9%, "
    "+6.2%); insiders sold $17.8M.",
    "IMPROVING", "STRONG",
    ["Revenue +37.9% YoY, operating income +83.1% — datacenter GPU share gains are real",
     "Beat streak intact; expectations score 90.6"],
    ["ROIC still just 6.8% — the growth has not yet earned through the acquisition goodwill",
     "P/E 172 on GAAP EPS"],
    ["MI-series datacenter share vs NVDA", "Whether 2027 AI capex digests or grows"],
    sc((6.0, 35.0), (9.0, 40.0), (13.0, 50.0), 0.30, 0.45, 0.25),
    "The business case is strong; the price case requires sustaining >50%/yr "
    "for five years — faster than consensus, faster than delivered, at a "
    "scale where the customer set is five hyperscalers with their own "
    "silicon programs. Probability-weighted value sits well below price.",
    ["If AI inference demand compounds as bulls project, even 52%/yr could be met for 2-3 years"],
    ["Five years of >50% growth — beyond consensus's own +33.7%"],
    ["Earnings 2026-08-04", "MI-roadmap customer announcements"],
    ["Hyperscaler custom silicon substituting merchant GPUs",
     "AI-capex digestion year would compress both E and the multiple"],
    ["Datacenter revenue growth below 30%", "A hyperscaler announcing volume custom-chip substitution",
     "Gross margin below 45%"],
    "The +213% year priced AMD as the structural #2 in an infinite AI "
    "buildout. Semis history says buildouts digest; when this one does, a "
    "172× GAAP P/E has no floor from value buyers. Insider sales, while "
    "modest, do not contradict that reading.",
    ["Bull case needs 50× on $13 EPS — a $650 price target resting on multiple AND earnings perfection",
     "Scenario EPS assumes GPU margins hold against custom-silicon pricing pressure"],
    ["Goodwill-heavy balance sheet keeps ROIC depressed (6.8%)"],
    ["P/E 172 GAAP / EV/revenue 22.5 vs a 13.6% delivered 3-year CAGR"],
    ["Client vs datacenter mix (segment pending)"],
    "UNATTRACTIVE", "LOW", 38,
    [C("Revenue grew 37.9% YoY with operating income +83.1%; the stock returned +213.5% over twelve months.",
       "FACT", "SEC:ACCESSION:0000002488-26-000076", "YAHOO:CHART:AMD"),
     C("Price-implied growth is 52.0%/yr for five years — above consensus's +33.7% next-FY expectation.",
       "FACT", "YAHOO:CHART:AMD", "FMP:ESTIMATES:AMD"),
     C("The valuation requires perfection through a potential AI-capex digestion cycle.",
       "INFERENCE", "YAHOO:CHART:AMD"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["AVGO"] = S(
    "AI-networking and custom-ASIC leader at a $1.82T market cap: revenue "
    "+47.9% YoY, operating margin 43.4%, and a price requiring 35.5%/yr vs "
    "24.4% delivered. Two flags the size of the market cap: receivables "
    "grew 46.8pt faster than revenue, and SBC is 11.6% of revenue. "
    "Consensus plan-gated.",
    "IMPROVING", "STRONG",
    ["Revenue +47.9% YoY; FCF $32.8B at a 43.4% margin — elite economics at scale",
     "Custom-ASIC franchise is the structural alternative to GPU monoculture"],
    ["Receivables +46.8pt vs revenue growth — the largest working-capital divergence among mega-caps",
     "Net debt $45.3B from serial acquisitions"],
    ["Custom-ASIC order book from hyperscalers", "VMware monetization limits"],
    sc((5.5, 35.0), (7.5, 45.0), (9.5, 55.0), 0.30, 0.50, 0.20),
    "The best-positioned AI-semi after NVDA — and priced accordingly at "
    "55× FCF with an 11pt gap between implied and delivered growth. The "
    "receivables build is the quiet risk: if AI customers are being "
    "vendor-financed into orders, reported growth overstates demand.",
    ["Custom silicon taking share from merchant GPUs could sustain 35%+ growth longer than cycles suggest"],
    ["~35%/yr for five years at $1.8T scale"],
    ["Quarterly AI-revenue disclosure", "Hyperscaler ASIC design wins"],
    ["Receivables divergence resolving as charge-offs or growth reversal",
     "Acquisition integration fatigue with $45B debt"],
    ["Receivables gap above 30pt again next quarter", "AI revenue growth below 30%",
     "FCF margin below 38%"],
    "A 46.8pt receivables-revenue divergence at this scale has two "
    "explanations — extended payment terms to win ASIC deals, or demand "
    "pull-forward — and both end with growth normalizing violently. "
    "Combined with 11.6% SBC and $45B of debt, the quality "
    "underneath the AI halo is weaker than the P&L suggests.",
    ["Base case takes reported revenue at face value despite the receivables signal",
     "55× FCF multiple persistence assumed"],
    ["Receivables +46.8pt vs revenue; SBC 11.6% of revenue"],
    ["EV/revenue 24.7 for a 24.4% delivered CAGR"],
    ["Payment-terms disclosure in notes (not parsed)"],
    "WATCH", "LOW", 45,
    [C("Revenue grew 47.9% YoY with a 43.4% operating margin and $32.8B TTM FCF.",
       "FACT", "SEC:ACCESSION:0001730168-26-000054"),
     C("Receivables grew 46.8 points faster than revenue — the largest mega-cap divergence in the universe.",
       "FACT", "SEC:ACCESSION:0001730168-26-000054"),
     C("Vendor financing or demand pull-forward may be inflating reported AI growth.",
       "INFERENCE", "SEC:ACCESSION:0001730168-26-000054"),
     C("Probability-weighted 12-month value is ~13% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["MU"] = S(
    "Memory maker at the apex of the HBM supercycle: revenue +345.7% YoY, "
    "operating margin 65.6% (vs a through-cycle history of losses), stock "
    "+729% in a year to a $1.04T market cap. The 20.9× P/E is the classic "
    "memory-cycle illusion — cheap multiples on peak earnings. Price "
    "requires 24.9%/yr vs 6.7% delivered through-cycle.",
    "IMPROVING", "STRONG",
    ["HBM sold out through contract windows; pricing power unprecedented for commodity memory",
     "Net cash $25.4B accumulated in a single cycle year"],
    ["Receivables +44.0pt vs revenue growth — even supercycles shouldn't need vendor terms",
     "65.6% operating margins in MEMORY have never persisted in industry history"],
    ["HBM supply agreements vs competitor capacity additions", "DRAM spot-price trajectory"],
    sc((20.0, 12.0), (35.0, 16.0), (55.0, 20.0), 0.35, 0.45, 0.20),
    "Every memory supercycle ends the same way: competitor capacity "
    "arrives, pricing collapses, and peak-earnings multiples become "
    "trough-earnings multiples at half the price. This one is bigger and "
    "AI-fed, but Samsung and SK are adding HBM lines as fast as physics "
    "allows. The through-cycle earnings power is a fraction of TTM.",
    ["If HBM stays supply-constrained through 2028, peak earnings persist long enough to grow into the price"],
    ["Permanent supercycle: 25%/yr against a 6.7% through-cycle history"],
    ["DRAM contract pricing monthly", "Competitor HBM qualification announcements"],
    ["Capacity response is already funded and public",
     "One AI-capex pause turns HBM allocation into HBM inventory"],
    ["DRAM spot prices declining two consecutive months", "HBM competitor share gains",
     "Receivables gap persisting above 25pt"],
    "At $1.04T, Micron is priced as a structural franchise when it is a "
    "cyclical commodity producer having its best year ever. The 20.9× "
    "'cheap' P/E on 65% margins would be 60×+ on normalized margins. "
    "Cycle math, not story, is the entire case.",
    ["Even the bear case (EPS $20) assumes margins far above through-cycle norms",
     "Scenario multiples assume the market keeps paying cycle-peak earnings as if durable"],
    ["Receivables +44pt vs revenue at cycle peak is a demand-quality warning"],
    ["Cheap-on-peak-earnings is the most reliable value trap in semis"],
    ["HBM vs commodity DRAM mix (segment pending)"],
    "UNATTRACTIVE", "LOW", 30,
    [C("Revenue grew 345.7% YoY with a 65.6% operating margin; the stock returned +729% in twelve months.",
       "FACT", "SEC:ACCESSION:0000723125-26-000015", "YAHOO:CHART:MU"),
     C("Receivables grew 44.0 points faster than revenue at the cycle peak.",
       "FACT", "SEC:ACCESSION:0000723125-26-000015"),
     C("The 20.9× P/E reflects peak-cycle earnings; normalized margins imply a far higher effective multiple.",
       "INFERENCE", "SEC:ACCESSION:0000723125-26-000015"),
     C("Probability-weighted 12-month value is well below the current price on cycle normalization.",
       "FORECAST")])

SPECS_EXTRA2["INTC"] = S(
    "Turnaround at a +346% twelve-month price: foundry hopes have re-rated "
    "Intel to a $466B market cap while TTM EPS is still NEGATIVE (-$2.21), "
    "operating margin -0.1%, and the reverse DCF requires 70.7%/yr — the "
    "most extreme requirement in the universe. Surprise history (+1429%) "
    "is a near-zero-base artifact; the expectations score is discounted "
    "to zero analytically.",
    "IMPROVING", "WEAK",
    ["Revenue +25.4% YoY off the bottom; operating income improving from deep losses",
     "Foundry option now carries government + hyperscaler backing"],
    ["Still lossmaking on TTM EPS; ROIC ~0",
     "Receivables +45.5pt vs revenue growth"],
    ["18A/14A node execution vs TSMC", "External foundry customer volume commitments"],
    sc(35.0, 60.0, 100.0, 0.35, 0.45, 0.20),
    "The market has pre-paid for a foundry renaissance that manufacturing "
    "history says is the hardest thing in industry: catching TSMC from "
    "behind while losing money. At 70.7%/yr implied, even success may not "
    "clear the bar. The machine scores what is filed; what is filed is a "
    "lossmaking company at 8.5× revenue.",
    ["If 18A yields land external whales, the re-rating extends regardless of current losses"],
    ["A complete, rapid foundry turnaround — ~71%/yr implied"],
    ["Earnings 2026-10-22", "Node-yield and customer announcements"],
    ["Yield misses would unwind the entire re-rating",
     "Capex intensity keeps FCF near zero even in success scenarios"],
    ["18A customer defection", "Gross margin below 35%", "Foundry revenue guidance cut"],
    "A +346% year on negative earnings is sentiment, not analysis: the "
    "receivables build, zero ROIC, and 0.6% FCF yield describe a company "
    "consuming capital, priced for one that compounds it. The asymmetry "
    "of the priced expectations is the bear case.",
    ["Scenario fair values are judgment on option value, not earnings math — stated plainly",
     "Surprise percentages are meaningless off near-zero bases"],
    ["Negative OCF/NI (-1.32); receivables +45.5pt"],
    ["8.5× revenue for negative earnings; implied growth without precedent"],
    ["Foundry vs products economics (segment pending)"],
    "UNATTRACTIVE", "MEDIUM", 28,
    [C("TTM diluted EPS is -$2.21 with a -0.1% operating margin, while the stock returned +346%.",
       "FACT", "SEC:ACCESSION:0000050863-26-000157", "YAHOO:CHART:INTC"),
     C("Price-implied growth is 70.7%/yr — the most extreme requirement in the coverage universe.",
       "FACT", "YAHOO:CHART:INTC"),
     C("The re-rating prices foundry success that filings do not yet evidence.",
       "INFERENCE", "SEC:ACCESSION:0000050863-26-000157"),
     C("Probability-weighted 12-month value is far below the current price.",
       "FORECAST")])

SPECS_EXTRA2["AMAT"] = S(
    "Semicap leader re-rated +190.9% in a year to the 100th percentile of "
    "its own valuation history: price now requires 45.4%/yr against 3.2% "
    "delivered — a 38.1pt gap, third-widest in the universe. The equipment "
    "cycle is real; the price assumes it never digests. Insiders sold "
    "$174M.",
    "IMPROVING", "MODERATE",
    ["Revenue +11.4% YoY; equipment backlog from AI-fab buildouts",
     "ROIC 28.4%, disciplined model through cycles"],
    ["OCF/NI 0.94 — cash slightly trailing booked earnings",
     "Insider selling $174M during the re-rating"],
    ["WFE spending trajectory 2027", "China export-control exposure"],
    sc((9.0, 25.0), (12.0, 30.0), (15.0, 38.0), 0.30, 0.50, 0.20),
    "Semicap is a cyclical toll booth priced as a perpetual-growth "
    "franchise: 50× earnings at the 100th valuation percentile for a "
    "business that grew 3.2%/yr through the last cycle. The AI-fab "
    "buildout is real and already in the price twice over.",
    ["A multi-year sovereign-fab buildout could extend the cycle beyond precedent"],
    ["~45%/yr growth — 14× the delivered rate"],
    ["WFE guidance updates", "China policy changes"],
    ["Equipment digestion cycles are violent: -30% revenue years are normal",
     "Export controls could remove a double-digit revenue slice"],
    ["Backlog declining two quarters", "WFE industry guidance cut", "China revenue restrictions widening"],
    "Equipment stocks at 100th-percentile valuations into a capacity "
    "digestion have one historical outcome. The $174M of insider selling "
    "into the re-rating is the tell: the people with fab-utilization "
    "visibility are reducing exposure.",
    ["Base case multiple (30×) is itself above the historical semicap norm"],
    ["None material"],
    ["100th-percentile P/E on cycle-peak earnings"],
    ["Logic vs memory equipment mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 33,
    [C("The stock returned +190.9% in twelve months; the P/E sits at the 100th percentile of five-year history.",
       "FACT", "YAHOO:CHART:AMAT"),
     C("Price-implied growth is 45.4%/yr vs 3.2% delivered 3-year CAGR.",
       "FACT", "YAHOO:CHART:AMAT", "SEC:ACCESSION:0001628280-26-037227"),
     C("Insiders sold $174.1M during the re-rating window.",
       "FACT", "SEC:FORM4:AMAT"),
     C("Probability-weighted 12-month value is ~30% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["LRCX"] = S(
    "Semicap etch/deposition leader: +216.5% year, 57× earnings at the "
    "100th valuation percentile, price requiring 38.4%/yr vs 2.3% "
    "delivered. Operationally excellent (34.3% operating margin, ROIC "
    "62.9%); the identical cycle-peak argument as AMAT applies with a "
    "higher multiple.",
    "IMPROVING", "MODERATE",
    ["Revenue +23.8% YoY; NAND/HBM equipment demand surge",
     "ROIC 62.9% — the best capital efficiency in semicap"],
    ["Same cycle: 3-year delivered CAGR just 2.3%",
     "Insiders sold $31.2M"],
    ["Memory-fab capex follow-through", "China exposure"],
    sc((4.5, 28.0), (6.0, 34.0), (7.5, 42.0), 0.30, 0.50, 0.20),
    "Memory-equipment exposure gears LRCX to the MU supercycle — both "
    "directions. At 57× peak-adjacent earnings, the price requires the "
    "memory buildout to run for years without digestion, against an "
    "industry that has never once done that.",
    ["HBM capacity racing could extend memory capex beyond any prior cycle"],
    ["~38%/yr growth vs 2.3% delivered"],
    ["Memory-maker capex guidance", "WFE updates"],
    ["Memory capex is the most violent equipment cycle of all",
     "When MU's cycle turns, LRCX's orders turn with a lag"],
    ["Memory WFE guidance cut", "Backlog decline", "China restrictions widening"],
    "LRCX at 57× on memory-driven orders is a second-derivative bet on "
    "the most cyclical end market in technology, at the highest multiple "
    "in its own history, after a +217% year. The setup requires nothing "
    "less than the abolition of the memory cycle.",
    ["Base multiple of 34× exceeds any sustained historical level for this name"],
    ["None material"],
    ["100th-percentile valuation on peak-cycle orders"],
    ["Etch vs deposition mix and China % (pending)"],
    "UNATTRACTIVE", "MEDIUM", 32,
    [C("The stock returned +216.5% in twelve months to a 57.2× P/E at the 100th percentile of own history.",
       "FACT", "YAHOO:CHART:LRCX"),
     C("Price-implied growth is 38.4%/yr vs 2.3% delivered 3-year CAGR.",
       "FACT", "YAHOO:CHART:LRCX", "SEC:ACCESSION:0000707549-26-000022"),
     C("The valuation requires memory-capex cycles to stop existing.",
       "INFERENCE", "YAHOO:CHART:LRCX"),
     C("Probability-weighted 12-month value is ~33% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["TXN"] = S(
    "Analog leader with the widest implied-vs-delivered gap among semis "
    "after INTC: price requires 31.3%/yr against a -4.1% delivered CAGR "
    "(+55.3pt gap). Revenue is recovering (+22.8% YoY off the analog "
    "trough) and the market has extrapolated one recovery year into a "
    "decade at 42.5× earnings, 95th-percentile valuation. Insiders sold "
    "$115M.",
    "IMPROVING", "MODERATE",
    ["Analog cycle recovery: +22.8% YoY, operating income +47.8%",
     "300mm cost advantage compounding; 37.3% operating margins"],
    ["3-year delivered CAGR is NEGATIVE (-4.1%)",
     "Capex supercycle still suppressing FCF (2.1% yield)"],
    ["Industrial/auto analog restocking depth", "Capex-to-FCF inflection timing"],
    sc((5.5, 22.0), (7.0, 27.0), (8.5, 33.0), 0.30, 0.50, 0.20),
    "TI's fab-scale strategy is sound and the cycle is turning — but a "
    "+55pt gap between required and delivered growth is the widest "
    "expectations overshoot in the semis set. Even flawless cycle recovery "
    "reaches perhaps a third of what the price requires.",
    ["The 300mm capacity bet could structurally lift share and margins beyond prior cycles"],
    ["31%/yr growth from a company that shrank 4%/yr for three years"],
    ["Industrial bookings recovery breadth", "Capex guidance step-down"],
    ["Analog recovery cycles are shallow; extrapolating the snapback overprices the norm",
     "China competition in commodity analog"],
    ["Bookings rollover", "Capex guidance NOT declining on schedule", "Gross margin below 55%"],
    "Paying 42× peak-recovery earnings at the 95th valuation percentile "
    "for a -4% CAGR business is expectation inflation, not analysis. The "
    "$115M insider selling into the re-rating aligns with the arithmetic.",
    ["Base assumes 27× persists — analog's historical band tops near 25×"],
    ["None material"],
    ["95th-percentile P/E on trough-recovery earnings"],
    ["End-market mix granularity (pending)"],
    "UNATTRACTIVE", "MEDIUM", 33,
    [C("Revenue grew 22.8% YoY in cycle recovery; the delivered 3-year CAGR is -4.1%.",
       "FACT", "SEC:ACCESSION:0000097476-26-000152"),
     C("Price-implied growth is 31.3%/yr — a +55.3pt gap over delivered, at the 95th valuation percentile.",
       "FACT", "YAHOO:CHART:TXN"),
     C("Insiders sold $115.4M during the re-rating.",
       "FACT", "SEC:FORM4:TXN"),
     C("Probability-weighted 12-month value is ~33% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["ADI"] = S(
    "Analog #2 in the same cycle recovery as TXN: revenue +37.3% YoY off "
    "the trough against a -2.8% 3-year CAGR, at 55× earnings with a "
    "+22.0pt implied-vs-delivered gap. Sector base rates mildly favorable "
    "(53.8% outperformed). The recovery is real; the multiple assumes it "
    "is a new normal.",
    "IMPROVING", "MODERATE",
    ["Revenue +37.3% YoY; operating income +103.5% — deep-cycle recovery leverage",
     "Franchise margins (64.5% gross) intact through the downcycle"],
    ["Receivables +11.2pt vs revenue", "ROIC still 9.0% under acquisition goodwill"],
    ["Industrial/comms restocking depth", "Synergy capture vs Maxim goodwill"],
    sc((6.0, 30.0), (8.0, 35.0), (10.0, 42.0), 0.30, 0.50, 0.20),
    "Same shape as TXN at a slightly less extreme premium: excellent "
    "franchise, real recovery, and a price requiring the recovery rate to "
    "persist for five years. Cycle math argues most of the good news is "
    "spent.",
    ["Industrial automation secular demand could flatten the analog cycle"],
    ["26.3%/yr implied vs -2.8% delivered"],
    ["Bookings commentary at next print", "Auto/industrial channel inventory data"],
    ["Restocking cycles end; run-rate extrapolation is the classic analog trap"],
    ["Bookings rollover", "Receivables gap above 15pt", "Gross margin below 60%"],
    "A 55× multiple on recovery-year earnings for a business that shrank "
    "through the prior three years prices a structural change no filing "
    "evidences. Insider selling ($33.6M, zero buys) does not dissent.",
    ["Base case multiple 35× vs analog historical band ~25×"],
    ["Receivables modestly outrunning revenue"],
    ["55× P/E, 65th percentile, for a shrinking-through-cycle business"],
    ["End-market mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 36,
    [C("Revenue grew 37.3% YoY in recovery; the 3-year delivered CAGR is -2.8%.",
       "FACT", "SEC:ACCESSION:0000006281-26-000052"),
     C("The shares trade at 55.0× TTM earnings; price-implied growth exceeds delivered by 22.0 points.",
       "FACT", "YAHOO:CHART:ADI"),
     C("The multiple treats a cyclical recovery as a structural re-rating.",
       "INFERENCE", "YAHOO:CHART:ADI"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["QCOM"] = S(
    "Handset-chip franchise the AI rally forgot: -3.5% revenue YoY, flat "
    "3-year CAGR, 18× earnings, 7.1% FCF yield — and a price implying "
    "0.6%/yr, almost exactly what the business delivers. The rare "
    "correctly-priced stock in the universe. Diversification (auto/IoT) "
    "is the free option.",
    "STABLE", "WEAK",
    ["FCF $12.5B (28.1% margin); capital returns consistent",
     "Auto design-win pipeline compounding off a small base"],
    ["Handset concentration with Apple modem insourcing overhang",
     "Revenue -3.5% YoY this cycle"],
    ["Android flagship cycles", "Auto/IoT revenue scaling to relevance"],
    sc((8.0, 13.0), (9.5, 16.0), (11.0, 20.0)),
    "Zero-expectations pricing for a zero-growth business with real "
    "optionality: fair on every axis. The 7.1% FCF yield pays the wait; "
    "nothing in the price needs auto/IoT to work — if it does, it's "
    "upside.",
    ["Auto backlog conversion could add a second franchise the price ignores"],
    ["Approximately nothing: 0.6%/yr implied"],
    ["Apple modem-transition disclosures", "Auto revenue milestones"],
    ["Apple insourcing removes a large licensing/chip slice on a known clock",
     "Android market maturity"],
    ["Licensing renegotiation adverse outcome", "Auto backlog cancellations"],
    "QCOM is fairly priced for its risks, which is the problem — the Apple "
    "modem loss is a scheduled earnings cliff, and 'optionality' has been "
    "the QCOM bull case for a decade without ever mattering to the "
    "multiple.",
    ["Auto option value is asserted, not evidenced in current revenue mix"],
    ["None material"],
    ["Fair value, not cheap: correctly-priced is not a thesis"],
    ["Licensing vs chip segment margins (pending)"],
    "WATCH", "MEDIUM", 52,
    [C("TTM FCF is $12.5B (7.1% yield); revenue declined 3.5% YoY.",
       "FACT", "SEC:ACCESSION:0000804328-26-000061"),
     C("Price-implied growth (0.6%/yr) matches delivered (0.1%) almost exactly.",
       "FACT", "YAHOO:CHART:QCOM"),
     C("The stock is fairly priced; the auto/IoT option comes free but has not yet mattered.",
       "INFERENCE", "SEC:ACCESSION:0000804328-26-000061"),
     C("Probability-weighted 12-month value approximates the current price.",
       "FORECAST")])

SPECS_EXTRA2["CSCO"] = S(
    "Networking incumbent re-rated +69.7% on AI-datacenter switching: now "
    "38× earnings at the 100th percentile of its own history, price "
    "requiring 24.8%/yr against 3.2% delivered (+23.4pt gap). Beats are "
    "steady but small (+1.8%, +2.0%, +2.9%); receivables +10.8pt vs "
    "revenue.",
    "IMPROVING", "MODERATE",
    ["Revenue +12.0% YoY with operating income +23.7% — AI switching orders are real",
     "Beat cadence steady; expectations score 78"],
    ["3-year delivered CAGR 3.2% — the franchise grows at GDP through cycles",
     "Receivables +10.8pt vs revenue"],
    ["AI-ethernet share vs InfiniBand", "Enterprise refresh cycle breadth"],
    sc((2.8, 20.0), (3.3, 26.0), (3.8, 32.0), 0.30, 0.50, 0.20),
    "Cisco at the 100th valuation percentile is a sentence that has "
    "historically self-corrected: the AI-switching order book is genuine "
    "but grafted onto a GDP-growth enterprise franchise, and the +23pt "
    "expectations gap prices the graft as the whole tree.",
    ["Ethernet displacing InfiniBand in AI clusters could double the addressable growth"],
    ["24.8%/yr from a 3.2% deliverer"],
    ["Earnings 2026-08-12; AI order disclosure", "Ethernet-vs-InfiniBand design wins"],
    ["AI switching is lumpy hyperscaler capex — the same digestion risk as semis",
     "Core enterprise networking remains ex-growth"],
    ["AI order book flattening", "Receivables gap above 15pt", "Revenue growth below 6%"],
    "The last time Cisco traded at its valuation ceiling on infrastructure "
    "euphoria was 2000; the business then grew into a fraction of the "
    "price over a decade. Smaller stakes this time, same shape: peak "
    "multiple on cyclical orders.",
    ["Base case holds a 26× multiple the company last sustained in the dot-com era"],
    ["Receivables outrunning revenue by 10.8pt"],
    ["100th-percentile P/E for a GDP-growth franchise"],
    ["AI vs enterprise order mix (pending)"],
    "UNATTRACTIVE", "MEDIUM", 37,
    [C("Revenue grew 12.0% YoY; the stock returned +69.7% to a 38.0× P/E at its 100th valuation percentile.",
       "FACT", "SEC:ACCESSION:0000858877-26-000078", "YAHOO:CHART:CSCO"),
     C("Price-implied growth is 24.8%/yr vs 3.2% delivered.",
       "FACT", "YAHOO:CHART:CSCO"),
     C("The AI-switching order book is priced as the whole franchise.",
       "INFERENCE", "SEC:ACCESSION:0000858877-26-000078"),
     C("Probability-weighted 12-month value is ~26% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["DELL"] = S(
    "AI-server assembler up +237.7% in a year: revenue +87.5% YoY on GPU "
    "server pass-through — and receivables grew 76.7pt faster than "
    "revenue, the single largest working-capital divergence ever recorded "
    "in this universe. Thin-margin (7.9% OI) hardware assembly at 34× "
    "earnings with $349M of insider selling.",
    "IMPROVING", "WEAK",
    ["AI-server backlog converting to revenue at extraordinary pace",
     "ISG franchise relationships with every enterprise buyer"],
    ["Receivables +76.7pt vs revenue — vendor financing at unprecedented scale",
     "7.9% operating margin on pass-through GPU content"],
    ["AI-server backlog sustainability", "Receivables collection quality"],
    sc((10.0, 18.0), (14.0, 24.0), (18.0, 30.0), 0.30, 0.50, 0.20),
    "Dell's AI-server boom is real revenue with borrowed quality: +77pt "
    "receivables divergence means nearly all incremental growth shipped "
    "on extended terms to capital-hungry AI buyers. If neocloud credit "
    "tightens, revenue and receivables unwind together. The +238% year "
    "prices none of that.",
    ["Enterprise AI adoption could broaden the buyer base beyond leveraged neoclouds"],
    ["AI-server growth persisting at margin AND payment terms both holding"],
    ["Quarterly backlog and receivables disclosure", "Neocloud funding-market conditions"],
    ["Concentrated exposure to leveraged GPU-cloud startups' credit",
     "Assembly margins invite competition at every layer"],
    ["Receivables gap above 40pt again", "AI backlog decline", "A neocloud customer credit event"],
    "A 76.7-point receivables-revenue divergence is what channel stuffing "
    "looks like in an honest company's filings: real orders, real product, "
    "financed by Dell because the buyers cannot pay cash. That is a credit "
    "portfolio at an equipment multiple. Insiders sold $349M.",
    ["Base case assumes the receivables convert to cash on schedule",
     "AI-server margins assumed stable despite hyperscaler direct-sourcing"],
    ["The receivables divergence dominates every other consideration"],
    ["34× earnings for 8%-margin assembly with embedded credit risk"],
    ["Customer concentration in receivables (not disclosed granularly)"],
    "UNATTRACTIVE", "MEDIUM", 30,
    [C("Revenue grew 87.5% YoY; receivables grew 76.7 points faster — the largest divergence in the universe's history.",
       "FACT", "SEC:ACCESSION:0001571996-26-000030"),
     C("The stock returned +237.7% in twelve months; insiders sold $349.4M.",
       "FACT", "YAHOO:CHART:DELL", "SEC:FORM4:DELL"),
     C("Growth is being vendor-financed to capital-constrained AI buyers — a credit portfolio at an equipment multiple.",
       "INFERENCE", "SEC:ACCESSION:0001571996-26-000030"),
     C("Probability-weighted 12-month value is ~25% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["HPQ"] = S(
    "PC/print cash cow at 9.3× earnings and a 16.1% FCF yield: the price "
    "implies -12.9%/yr against -4.3% delivered — pessimism priced beyond "
    "even the shrinkage trend. One flag: receivables +32.3pt vs revenue "
    "(PC channel terms). The AI-PC refresh is a free option at this "
    "price.",
    "STABLE", "WEAK",
    ["FCF $3.8B against a $24B market cap; buybacks retire ~7%/yr",
     "Revenue actually GREW +9.0% this year against the priced decline"],
    ["Receivables +32.3pt vs revenue — channel terms extending",
     "Print supplies decline is structural and permanent"],
    ["AI-PC refresh cycle magnitude", "Print decline rate vs cash harvesting"],
    sc((2.3, 7.0), (2.8, 10.0), (3.2, 12.0), 0.30, 0.50, 0.20),
    "Deep-value cash harvesting: at a 16% FCF yield with shares shrinking "
    "7%/yr, HPQ pays equity-like returns from buybacks alone even in "
    "decline. The receivables build needs watching, but the priced-in "
    "decline (-13%/yr) is roughly triple the delivered one.",
    ["An AI-PC refresh would make the priced decline arithmetic impossible"],
    ["Accelerating decline: -12.9%/yr vs -4.3% actual"],
    ["PC-refresh data points", "Capital-return announcements"],
    ["Channel-stuffing risk in the receivables build",
     "Print cash decay could accelerate past PC stabilization"],
    ["Receivables gap above 20pt again", "FCF below $3B", "PC units declining post-refresh"],
    "Cheap harvests can still rot: the +32pt receivables build suggests "
    "revenue is being pulled forward through the channel, and print — the "
    "profit pool — declines regardless of the PC cycle. Melting-ice-cube "
    "math only works if the melt rate is honest.",
    ["Assumes the +9% revenue print reflects demand, not channel loading"],
    ["Receivables +32.3pt vs revenue is the key quality question"],
    ["None — the multiple already prices heavy decline"],
    ["Channel inventory levels (not disclosed)"],
    "WATCH", "MEDIUM", 57,
    [C("The shares trade at 9.3× earnings with a 16.1% FCF yield; revenue grew 9.0% YoY.",
       "FACT", "SEC:ACCESSION:0000047217-26-000029"),
     C("Price-implied growth is -12.9%/yr — triple the delivered -4.3% decline rate.",
       "FACT", "YAHOO:CHART:HPQ"),
     C("Receivables grew 32.3 points faster than revenue — channel-loading risk.",
       "FACT", "SEC:ACCESSION:0000047217-26-000029"),
     C("Probability-weighted 12-month value is modestly above price; buyback math carries the return.",
       "FORECAST")])

SPECS_EXTRA2["MCD"] = S(
    "Franchise royalty machine: 46.3% operating margins, +9.4% revenue "
    "growth. DATA DEFECT STATED PLAINLY: the normalized diluted-EPS field "
    "for MCD is corrupt (a units-mapping bug producing a nonsensical "
    "value), so P/E is unavailable and all scenarios are built on "
    "FCF/share (~$9.86). Price implies 20.0%/yr vs 5.1% delivered.",
    "STABLE", "MODERATE",
    ["Operating margin 46.3% — the best business model in food",
     "Revenue +9.4% YoY on pricing and digital mix"],
    ["$38.9B net debt services the buyback machine",
     "Traffic softness at low-income cohorts persists"],
    ["Value-menu traffic recovery", "International franchise growth"],
    sc(180.0, 247.0, 322.0),
    "A royalty on global food spending priced at a 15pt premium to its "
    "delivered growth: the quality deserves a premium; this one (26.7× "
    "FCF, implied 20%/yr) is at the rich end with traffic already "
    "softening. Fair-to-expensive.",
    ["Pricing power could outrun traffic softness for years"],
    ["High-teens implied growth vs 5% delivered"],
    ["Quarterly comps and traffic mix", "Value-platform performance"],
    ["Low-income traffic erosion is a demand-quality signal",
     "GLP-1 tail risk on volume"],
    ["Comp traffic negative two quarters", "Franchisee health metrics deteriorating"],
    "At 26.7× FCF with softening traffic and $39B of debt, MCD is priced "
    "for a pricing-power permanence that GLP-1s and value-seeking "
    "consumers are both quietly eroding.",
    ["FCF-based scenarios required by the EPS data defect — per-share figures are approximations",
     "Debt-funded buybacks flatter per-share metrics"],
    ["MCD diluted-EPS normalization is corrupt (units bug) — flagged for repair; scenarios avoid it"],
    ["26.7× FCF at a 15pt implied-growth premium"],
    ["EPS tag repair (queued data fix)"],
    "WATCH", "LOW", 47,
    [C("Operating margin is 46.3% with revenue +9.4% YoY; net debt is $38.9B.",
       "FACT", "SEC:ACCESSION:0000063908-26-000051"),
     C("The normalized diluted-EPS field for MCD is corrupt (units-mapping defect); scenarios use FCF/share.",
       "FACT", "SM:DATA_QUALITY:MCD"),
     C("Price-implied growth (20.0%/yr) carries a ~15pt premium over delivered (5.1%).",
       "FACT", "YAHOO:CHART:MCD"),
     C("Probability-weighted 12-month value is ~6% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["SBUX"] = S(
    "Turnaround priced as accomplished: 78.9× depressed earnings, price "
    "implying 31.1%/yr vs 4.9% delivered (+32.6pt gap), while the "
    "expectations score (17) is the WORST in the universe — recent "
    "surprises were misses (-6.5%, -4.4%) before one beat. The "
    "turnaround may work; the stock has already been paid for it.",
    "MIXED", "WEAK",
    ["Operating income +37.8% YoY off the trough — early repair evidence",
     "Brand pricing power intact internationally"],
    ["Expectations score 17: the only chronic misser in the coverage set",
     "78.9× earnings on depressed EPS leaves no multiple room"],
    ["Labor-model economics vs throughput", "China franchise trajectory"],
    sc((1.2, 30.0), (1.8, 35.0), (2.5, 40.0), 0.30, 0.50, 0.20),
    "Every scenario that doesn't assume full margin restoration lands "
    "below the current price — even the bull case ($100) barely clears "
    "it. A turnaround stock where the bull case is break-even is a short "
    "expectations position, not a long one.",
    ["A faster-than-modeled margin snap-back on labor efficiency"],
    ["A completed turnaround: 31%/yr implied"],
    ["Earnings 2026-07-29 (4 days)", "China same-store data"],
    ["Guidance misses have been the pattern, not the exception",
     "Union/labor cost structure ratcheting"],
    ["Another EPS miss", "China comps negative", "Margin guidance cut"],
    "The market pays 79× for a company that has missed most recent "
    "quarters, on faith in a turnaround whose costs (labor investment) "
    "are certain and whose benefits are hypothetical. The +32.6pt "
    "expectations gap is the widest consumer overshoot in the universe.",
    ["Even the bear multiple (30×) is generous for a chronic misser"],
    ["None material"],
    ["79× trailing earnings; bull-case fair value ≈ current price"],
    ["Labor-model unit economics (pending transcripts)"],
    "UNATTRACTIVE", "MEDIUM", 30,
    [C("The shares trade at 78.9× TTM earnings; price-implied growth is 31.1%/yr vs 4.9% delivered.",
       "FACT", "YAHOO:CHART:SBUX", "SEC:ACCESSION:0000829224-26-000080"),
     C("The expectations score (17) is the lowest in the universe: recent surprises -6.5%, -4.4%, +17.6%.",
       "FACT", "FMP:EARNINGS:SBUX"),
     C("The bull-case fair value approximately equals the current price — the turnaround is pre-paid.",
       "INFERENCE", "YAHOO:CHART:SBUX"),
     C("Probability-weighted 12-month value is ~40% below the current price.",
       "FORECAST")])

SPECS_EXTRA2["RIVN"] = S(
    "EV maker scaling through cash burn: revenue +11.4% YoY, gross margin "
    "just turned positive (+1.0%), FCF -$3.0B, and the NONOP flag is "
    "active. TWO insiders made discretionary purchases — the only "
    "positive-signal EV name. Scenario dispersion is the widest in the "
    "universe; conviction is capped at LOW structurally.",
    "MIXED", "WEAK",
    ["Gross margin crossed zero — the survival threshold",
     "Two-insider discretionary buying into the R2 ramp"],
    ["FCF -$3.0B/yr against ~$20B market cap — runway math dominates",
     "Operating margin -68.9%"],
    ["R2 volume ramp economics", "Capital raise timing and dilution"],
    sc(6.0, 15.0, 35.0, 0.35, 0.40, 0.25),
    "A binary: either R2 scales to positive unit economics before the "
    "cash runs out (bull: multiples of today's price) or dilution/distress "
    "repricing dominates (bear: -60%). Insiders betting on the former is "
    "the only edge signal available; it is not enough for conviction.",
    ["The insiders closest to the R2 cost curve are buying"],
    ["Partial success is priced; the tails are not"],
    ["Earnings 2026-07-30 (5 days)", "R2 production-rate milestones"],
    ["Capital-market access is the existential variable",
     "EV price war compressing the margin path"],
    ["Gross margin back below zero", "A dilutive raise below current price",
     "R2 ramp delay past two quarters"],
    "Pre-profit automakers at scale-up are financing bets, not valuation "
    "exercises: the -$3.0B burn requires flawless execution AND friendly "
    "markets, and the machine's fundamental framework simply does not "
    "apply cleanly. The honest classification is barely-scored.",
    ["All scenario values are capital-structure-dependent judgment",
     "Fundamental scoring is of limited validity for pre-profit scale-ups"],
    ["NONOP flag active; net income contains non-operating items"],
    ["EV/revenue 3.5 for negative-margin manufacturing"],
    ["Unit economics per platform (not disclosed granularly)"],
    "WATCH", "LOW", 42,
    [C("Gross margin turned positive (+1.0%) for the first time; FCF remains -$3.0B TTM.",
       "FACT", "SEC:ACCESSION:0001874178-26-000035"),
     C("Two insiders made discretionary open-market purchases in the trailing 6 months.",
       "FACT", "SEC:FORM4:RIVN"),
     C("The investment case is a financing binary that fundamental scoring cannot adjudicate.",
       "INFERENCE", "SEC:ACCESSION:0001874178-26-000035"),
     C("Scenario dispersion is the widest in the universe; conviction is structurally LOW.",
       "FORECAST")])
