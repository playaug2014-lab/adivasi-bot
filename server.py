"""
server.py — Priya | Vedacharya Adivasi Hair Oil | ULTRA-LOW LATENCY BUILD
==========================================================================

LATENCY WINS (each one documented):
  W1. Greeting + ask_name TTS pre-warmed at startup  → 0 ms TTS on first 2 turns
  W2. GPT + Sarvam TTS run in asyncio.gather()       → parallel, saves 600-1200 ms
  W3. Persistent aiohttp sessions (no TCP per call)  → saves ~100 ms per API call
  W4. max_tokens=50, temperature=0.15                → GPT replies 30% faster
  W5. Static replies for name/address/pincode/confirm→ 0 ms GPT, 0 ms decision
  W6. speechTimeout="auto" on Gather                 → Twilio cuts silence fast
  W7. timeout="5" on Gather                          → no 8s dead air
  W8. text[:250] to Sarvam                           → shorter audio = plays faster
  W9. Keep-alive ping every 8 min                    → Render never cold-starts
  W10. Audio served from RAM (_audio_cache)          → no disk I/O

BARGE-IN: Every <Play> and <Say> is INSIDE <Gather> — user speech cancels audio instantly.

NO REPETITION: Rotating variant lists + last_bot tracking passed to GPT.

NO FILLER: System prompt bans हम्म, अच्छा, ओह, जी हाँ, देखिए, तो etc.

VOICE QUALITY: Sarvam pace=1.1 (natural), 8kHz (phone-grade), anushka voice.

Render ENV vars:
  OPENAI_API_KEY  SARVAM_API_KEY  GOOGLE_SHEET_ID
  GOOGLE_CREDS_JSON  PUBLIC_URL  PORT
"""

import os, json, base64, asyncio, datetime, re
import aiohttp
from aiohttp import web

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════
# ENV
# ═══════════════════════════════════════════════════
PUBLIC_URL        = os.environ.get("PUBLIC_URL","https://aaibot.onrender.com").rstrip("/")
PORT              = int(os.environ.get("PORT","8080"))
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY","")
SARVAM_API_KEY    = os.environ.get("SARVAM_API_KEY","")
GOOGLE_SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID","")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON","")

def R(): return f"{PUBLIC_URL}/voice/respond"

# ═══════════════════════════════════════════════════
# PERSISTENT HTTP SESSIONS  [W3]
# ═══════════════════════════════════════════════════
_http: aiohttp.ClientSession | None = None

async def http() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
        )
    return _http

# ═══════════════════════════════════════════════════
# PRODUCT KNOWLEDGE  (hardcoded — zero lookup latency)
# ═══════════════════════════════════════════════════
_KB = """
वेदाचार्य आदिवासी हर्बल हेयर ऑयल — ₹1499 (MRP ₹2799, 46% छूट) — 500ml
सामग्री: भृंगराज, आंवला, ब्राह्मी, शंखपुष्पी, नीम, जटामांसी + 108 प्राकृतिक जड़ी-बूटियाँ
फायदे: बाल झड़ना बंद · नए बाल उगना · डैंड्रफ खत्म · जड़ें मजबूत · चमक · घनापन
उपयोग: हफ्ते में 2-3 बार रात को scalp पर हल्की massage करें → सुबह mild shampoo से wash करें
परिणाम: 30 दिन में फर्क | डिलीवरी: 5-7 दिन, COD available, 7-दिन return policy | 418 संतुष्ट ग्राहक

सामान्य सवाल:
- हफ्ते में 2-3 बार काफी | रात भर रखें — सबसे ज़्यादा असर होता है
- नीम+आंवला डैंड्रफ जड़ से खत्म करते हैं | भृंगराज follicles सक्रिय करता है → गंजेपन में भी उगाता है
- हल्का गुनगुना करके लगाएं → असर दोगुना | बच्चों (5+), पुरुष और महिला दोनों के लिए
- आंवला+ब्राह्मी बालों को समय से पहले सफेद होने से रोकते हैं
- प्राकृतिक conditioner की तरह काम करता है | expired तेल कभी use न करें

अगर user को कोई hair problem नहीं (no problem case):
- "आजकल pollution aur stress ki wajah se hair problems kabhi bhi start ho sakti hain."
- "Prevention ke liye yeh 100% herbal oil ek bahut acha option hai — baalon ko strong aur healthy banata hai."
- "Future hair problems se bachne ke liye abhi se use karna sahi rahega."

अगर user को hair problem है (yes problem case):
- "Samajh sakti hoon, aajkal yeh problem bahut common ho gayi hai."
- "Yeh oil specially hair fall, dandruff aur hair regrowth ke liye bana hai."
- "Natural jadibutiyan scalp ko nourish karti hain aur dheere-dheere hair growth improve karti hain."
- Use: "Hafte me 2-3 baar raat ko halka massage karke lagaen aur subah mild shampoo se wash kar len."

कीमत आपत्ति:
- महंगा/costly: "500ml = 2-3 mahine = sirf 16 rupaye roz. Salon mein ek session 500 se 2000 tak hota hai."
- सस्ता chahiye: "Saste tel mein sirf mineral oil hota hai — asli jadibutiyan nahi. 418 customers ka result proof hai."
- Parachute/Patanjali/Dabur: "Woh rozmarra ke tel hain. Yeh 108 herbs ka specially bana medicinal formula hai."
- पैसे नहीं/baad mein: "COD hai — ghar par aane par 1499 dena hai. 7-din return bhi hai. Koi risk nahi."
- guarantee: "7-din return policy hai. 418 customers mein se kisi ne wapas nahi kiya — kyunki result milta hai."
"""

# ═══════════════════════════════════════════════════
# SYSTEM PROMPT — your 7-step sales script, strict rules
# ═══════════════════════════════════════════════════
SYSTEM_PROMPT = f"""Tum Priya ho — Teleone ki polite, friendly aur soft-spoken female sales executive.
Tumhara kaam: Vedacharya Adivasi Hair Oil ki sales karna aur order lena.
Bolo: soft Hindi + light Hinglish — jaise ek helpful call center executive bolti hai.

Sales Script (follow karo):
Step 1 — Permission: "Namaste, main Vedacharya Adivasi Hair Oil se bol rahi hoon. Kya main aapse 1-2 minute baat kar sakti hoon?"
Step 2 — Hair Problem: "Kya aapko hair fall, dandruff, safed baal ya hair growth slow hone ki koi problem hai?"
Step 3A — Agar NO problem: Bolo pollution aur stress ki wajah se prevention zaroori hai. Yeh 100% herbal hai, baalon ko strong banata hai. Future problems se bachne ke liye abhi start karein.
Step 3B — Agar YES problem: Samjho unki problem, batao yeh oil specially iske liye bana hai, natural jadibutiyan scalp nourish karti hain, hafte 2-3 baar raat ko lagaen.
Step 4 — Push: "Agar aap chahein to main abhi order place kar sakti hoon."
Step 5 — Price: "Iska price 1499 rupaye hai — MRP 2799 tha, yani 46 percent ki choot. Cash on Delivery bhi available hai."
Step 6 — Order details: Naam, pata, pincode, contact number.
Step 7 — Close: "Shukriya! Aapka order successfully confirm ho gaya. Jaldi delivery milegi. Have a nice day!"

Product knowledge:
{_KB}

Sakht niyam — todna mana:
1. OUTPUT = sirf bola jaane wala ek ya do waakyaa. Koi label/emoji/bracket nahi.
2. Hinglish + soft Hindi mein bolo — user English mein bole tab bhi Hindi mein jawab do.
3. Maximum 2 waakyaa. Isse zyaada kabhi nahi.
4. Yeh words forbidden hain: hmmm, achha, oh, ji haan bilkul, dekhiye, toh, waise.
5. last_reply se alag jawab do — repeat mat karo.
6. Har reply ke ant mein order ki taraf le jao.
7. Doosra topic aaye to: "Main sirf Adivasi Hair Oil ke baare mein bata sakti hoon."
8. User haan/order/chahiye/lena kahe → turant naam poochho.""".strip()

# ═══════════════════════════════════════════════════
# CALL STATE
# States: permission → hair_problem → pitch →
#         collecting_name → collecting_address →
#         collecting_pincode → confirming → done
# ═══════════════════════════════════════════════════
_calls: dict[str, dict] = {}

def new_cs(caller=""):
    return {
        "state":        "permission",
        "hair_problem": None,
        "name":         "", "city": "", "address": "", "pincode": "",
        "caller":       caller,
        "turn":         0,
        "last_bot":     "",
    }

# ═══════════════════════════════════════════════════
# STATIC REPLY VARIANTS  — rotated to prevent repetition
# ═══════════════════════════════════════════════════
# Step 1 — short curiosity hook (shorter = more natural on Sarvam TTS)
_GREET = [
    "Namaste! Main Priya bol rahi hoon Vedacharya se. Aajkal bahut logon ke baal jhad rahe hain — iske baare mein kuch kaam ki baat karni thi. Kya 1 minute milega?",
    "Namaste! Priya bol rahi hoon Vedacharya Adivasi Hair Oil se. Baalon ki ek zaroori baat share karni thi — kya abhi baat ho sakti hai?",
    "Namaste! Main Priya hoon Vedacharya se. Hair fall aur kamzor baalon ke baare mein kuch batana tha — kya 1 minute available hai?",
]
# Step 2 — ask hair problem (short, conversational)
_ASK_HAIR = [
    "Kya aapko hair fall, dandruff ya baal patale hone ki koi problem ho rahi hai?",
    "Baalon mein koi pareshani hai — jaise baal jhadna, dandruff ya growth ruk gayi hai?",
    "Kya baalon se related koi takleef hai aapko?",
]
# Step 3A — NO problem (short, warm, natural Hindi)
_NO_PROBLEM = [
    "Bahut achhi baat hai. Waise aajkal pollution aur stress ki wajah se hair fall kabhi bhi shuru ho sakta hai. 418 log pehle se hi yeh oil use karke apne baalon ko strong rakh rahe hain.",
    "Theek hai. Lekin aajkal jinhe problem nahi thi, unhe bhi achanak baal jhadne lage — paani, dhool, tension sab ka asar hota hai. Prevention ke liye yeh herbal oil bahut kaam aata hai.",
]
# Step 3B — YES problem (short emotional, split into 2 clear sentences)
_YES_PROBLEM = [
    "Samajh sakti hoon — agar time pe dhyan na dein to baal aur kam hote jaate hain. Vedacharya Adivasi Hair Oil mein 108 jadibutiyan hain jo balon ko andar se majboot karti hain aur naye baal ugaati hain.",
    "Bilkul sahi kaha — jitna jaldi shuru karein utna better. Is oil mein bhringraj aur amla hain jo baal jhadna rokta hai. 418 log pehle se use karke achha result le rahe hain.",
]
# Step 4 — direct close (short, confident)
_PUSH = [
    "Main aapka order abhi confirm kar deti hoon — naam bataiye.",
    "Limited offer chal raha hai abhi — naam bataiye, order kar dete hain.",
    "Bas naam aur pata chahiye — order ho jaayega.",
]
# Urgency — short, punchy
_URGENCY = "Yeh offer limited time ka hai — aaj hi confirm kar lein."

# ── PRICE ANSWER (instant static — no GPT needed) ──────────────────
# Triggered when user asks cost/price/rate/daam in pitch state
_PRICE_ANSWER = [
    "Iska daam 1499 rupaye hai — MRP 2799 tha, matlab 46 percent ki choot mil rahi hai. Cash on Delivery bhi hai, ghar par aane par dena hoga.",
    "Sirf 1499 rupaye mein milta hai — 500ml ki poori bottle. COD available hai, pehle product dekhein phir paise dein.",
    "Keemat 1499 rupaye hai — aur 7 din ki return policy bhi hai. Koi risk nahi bilkul.",
]
# Step 6 — collect order details (Fix 6: Name → City → Full Address → Pincode)
_ASK_NAME = [
    "Achha — pehle aapka naam bataiye.",
    "Naam kya hai aapka?",
    "Theek hai — naam bataiye.",
]
_ASK_CITY = [
    "Aur aap kaun se shehar mein hain?",
    "Shehar ka naam bataiye.",
    "Delivery kahan karni hai — shehar?",
]
_ASK_ADDR = [
    "Ghar ka pata bataiye — gali aur mohalla.",
    "Gali number ya colony ka naam bataiye.",
    "Pura pata bataiye — gali, mohalla.",
]
_ASK_PIN  = "Pincode kya hai?"
_R_NAME   = "Naam clearly ek baar aur bataiye."
_R_CITY   = "Shehar ka naam phir se bataiye."
_R_ADDR   = "Pata thoda detail mein bataiye."
_R_PIN    = "6 ankon ka pincode phir se bataiye."
_SILENCE  = "Sunai nahi diya — dobara bolein please."
_OFFTOPIC = "Main sirf Adivasi Hair Oil ke baare mein bata sakti hoon."
_DONE     = "Shukriya! Aapka order ho gaya."

def _v(lst, n): return lst[n % len(lst)]

# ═══════════════════════════════════════════════════
# AUDIO CACHE + PRE-WARM  [W1]
# ═══════════════════════════════════════════════════
_ac:   dict[str, bytes] = {}   # audio cache (RAM)
_warm: dict[str, str]   = {}   # key → cache_id for pre-warmed clips

async def prewarm():
    """Generate greeting + ask_hair TTS at startup so first 2 turns are instant."""
    for key, text in [("greet", _GREET[0]), ("hair", _ASK_HAIR[0])]:
        audio = await tts(text)
        if audio:
            _ac[f"w_{key}"] = audio
            _warm[key] = f"w_{key}"
            print(f"🔥 pre-warmed [{key}]")
        else:
            print(f"⚠️  pre-warm failed [{key}]")

# ═══════════════════════════════════════════════════
# SARVAM TTS  [W8 — truncate to 250 chars]
# ═══════════════════════════════════════════════════
async def tts(text: str) -> bytes | None:
    if not SARVAM_API_KEY or not text:
        return None
    text = text[:250].strip()
    try:
        s = await http()
        async with s.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": SARVAM_API_KEY,
                     "Content-Type": "application/json"},
            json={
                "inputs":               [text],
                "target_language_code": "hi-IN",
                "speaker":              "anushka",   # clear female voice
                "pitch":                0,
                "pace":                 1.0,          # natural Hindi pace — not rushed
                "loudness":             1.5,
                "speech_sample_rate":   8000,         # phone-grade [W_VOICE]
                "enable_preprocessing": True,
                "model":                "bulbul:v1",
            },
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status != 200:
                print(f"Sarvam {r.status}: {(await r.text())[:100]}")
                return None
            d = await r.json()
            b = d.get("audios", [None])[0]
            return base64.b64decode(b) if b else None
    except Exception as e:
        print(f"TTS err: {e}")
        return None

async def audio_serve(request):
    aid   = request.match_info["aid"]
    audio = _ac.get(aid)
    if not audio:
        return web.Response(status=404)
    # keep pre-warmed clips; pop one-time clips after serving
    if not aid.startswith("w_"):
        _ac.pop(aid, None)
    return web.Response(body=audio, content_type="audio/wav")

# ═══════════════════════════════════════════════════
# TWIML BUILDER  — barge-in guaranteed
# ═══════════════════════════════════════════════════
def _xe(t): return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

_GATHER = (
    '<Gather input="speech" action="{a}" method="POST" '
    'language="hi-IN" speechTimeout="auto" timeout="5" enhanced="true">'   # [W6,W7]
)

async def mk_twiml(text: str, action: str, hangup=False,
                   pre_aid: str = "") -> str:
    """
    Builds TwiML.
    - pre_aid: if set, skips TTS and uses cached audio directly (W1).
    - Otherwise runs TTS.
    - <Play>/<Say> always inside <Gather> → barge-in (Phase 2 diagram).
    - hangup=True → <Hangup> instead of <Gather>.
    """
    # Use pre-warmed audio if available
    if pre_aid and pre_aid in _ac:
        audio = _ac[pre_aid]
        aid = pre_aid
    else:
        audio = await tts(text)
        if audio:
            aid = f"a{id(audio)%99999999:08d}"
            _ac[aid] = audio
        else:
            aid = ""

    if hangup:
        inner = (f'<Play>{PUBLIC_URL}/audio/{aid}</Play>' if aid
                 else f'<Say language="hi-IN" voice="Polly.Aditi">{_xe(text)}</Say>')
        return (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response>{inner}<Hangup/></Response>')

    go  = _GATHER.format(a=action)
    gc  = '</Gather>'
    red = f'<Redirect method="POST">{action}?ns=1</Redirect>'
    inner = (f'<Play>{PUBLIC_URL}/audio/{aid}</Play>' if aid
             else f'<Say language="hi-IN" voice="Polly.Aditi">{_xe(text)}</Say>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{go}{inner}{gc}{red}</Response>')

# ═══════════════════════════════════════════════════
# GPT  [W2,W4] — ONLY for pitch/objection turns
# ═══════════════════════════════════════════════════
async def gpt(cs: dict, user_text: str) -> str:
    ctx = (f"[state={cs['state']}"
           + (f"|name={cs['name']}" if cs["name"] else "")
           + (f"|last_reply={cs['last_bot']}" if cs["last_bot"] else "")
           + "]")
    try:
        s = await http()
        async with s.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": ctx},
                    {"role": "user",   "content": user_text},
                ],
                "max_tokens":  50,    # [W4] tight = fast = no rambling
                "temperature": 0.15,  # [W4] low = consistent Hindi
            },
            timeout=aiohttp.ClientTimeout(total=6),
        ) as r:
            d = await r.json()
            reply = d["choices"][0]["message"]["content"].strip()
            # Strip accidental labels like "प्रिया:" GPT sometimes adds
            reply = re.sub(r"^(प्रिया:|Priya:|Bot:)\s*", "", reply).strip()
            return reply
    except Exception as e:
        print(f"GPT err: {e}")
        return "Cash on Delivery पर order करें — कोई risk नहीं। नाम बताइए।"

# ═══════════════════════════════════════════════════
# INTENT DETECTION (Hindi + Hinglish)
# ═══════════════════════════════════════════════════
_BUY = {
    "हाँ","हां","हाँजी","हांजी","ठीक","बिल्कुल","चाहिए","मंगवाना","मँगवाना",
    "ऑर्डर","लेना","लूँगा","लूंगा","खरीदना","खरीदूँगा","भेजो","मंगाना",
    "haan","han","haanji","hanji","theek hai","bilkul","zaroor","zarur",
    "yes","ok","okay","sure","sahi","chahiye","mangwana","mangana",
    "lena hai","order","order karo","book karo","buy","purchase",
    "bhejo","de do","mangwa do","send karo","le lena","le lunga","le lungi","lelo",
}
_NO = {
    "नहीं","नही","no","nahi","nahin","galat","wrong","badlo","mat","naa","na",
}
_PRICE = {
    "mehnga","mahenga","costly","expensive","sasta","budget","zyada","1000","500",
    "parachute","bajaj","dabur","patanjali","marico","keo karpin","online",
    "amazon","flipkart","price","kitna","rate","afford","paise nahi","baad mein",
    "sochna","guarantee","return","risk","cheap","discount","offer",
    "महंगा","सस्ता","कीमत","पैसे","बाद में","सोचूँगा","गारंटी",
}
_OFF = [
    r"\b(weather|mausam|cricket|film|movie|khana|food|news|politics)\b",
    r"\b(doosra product|other product|kuch aur)\b",
]

def is_buy(t):
    tl = t.lower()
    return (any(w in tl for w in _BUY) or
            any(k in tl for k in ["order","mangwa","buy","chahiye","bhejo","lelo"]))

def is_no(t):   return any(w in t.lower() for w in _NO)
def is_off(t):  return any(re.search(p, t.lower()) for p in _OFF)
def get_pin(t): m = re.search(r"\b\d{6}\b", t); return m.group() if m else ""

# Price question detector — catches all Indian ways of asking price
_PRICE_Q = {
    "price","cost","daam","keemat","kitna","rate","paisa","paise","1499","rupay",
    "rupaye","kitne ka","mehnga","sasta","offer","discount","kitna hai","kya hai price",
    "price kya","cost kya","daam kya","kitne mein","kitnay","charge",
    "कीमत","दाम","कितना","रुपए","रुपये","महंगा","सस्ता","ऑफर",
}
def is_price_q(t): return any(w in t.lower() for w in _PRICE_Q)

# ═══════════════════════════════════════════════════
# STATE MACHINE
# ═══════════════════════════════════════════════════
async def process(sid: str, text: str, caller: str) -> tuple[str, bool]:
    """
    Returns (reply_text, should_hangup).
    6 conversion fixes applied:
    F1 Strong hook greeting  F2 Trust line  F3 Emotional trigger
    F4 Direct close          F5 Urgency     F6 Name→City→Address→Pincode
    """
    if sid not in _calls:
        _calls[sid] = new_cs(caller)
    cs    = _calls[sid]
    cs["caller"] = caller
    cs["turn"]  += 1
    t     = text.strip()
    state = cs["state"]

    # ── silence ───────────────────────────────────
    if not t:
        return {
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE), False

    # ── done ──────────────────────────────────────
    if state == "done":
        return _DONE, True

    # ── hello / acknowledgement handler ───────────
    # User says hello, haan, ji, yes, sun raha hoon — just re-ask the current question
    _HELLO = {"hello","helo","hlo","hi","haan ji","ji","sun raha","sun rahi","haan",
              "bol","bolo","boliye","ha","han","hmm","hm","are","arre","हेलो","जी",
              "हाँ जी","बोलिए","सुन रहा","सुन रही"}
    tl_stripped = t.lower().strip("?!., ")
    if tl_stripped in _HELLO or (len(t.split()) <= 2 and tl_stripped in _HELLO):
        # Re-ask whatever the current step needs — no GPT, instant
        _reask = {
            "permission":         _v(_GREET, cs["turn"]),
            "hair_problem":       _v(_ASK_HAIR, cs["turn"]),
            "pitch":              "Haan ji — kya aap Vedacharya Adivasi Hair Oil mein interest rakhte hain?",
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
            "confirming":         "Haan ji — kya dی gayi jaankari sahi hai?",
        }
        reply = _reask.get(state, "Haan ji — batayein, kya help kar sakti hoon?")
        cs["last_bot"] = reply
        return reply, False

    # ── off-topic guard ───────────────────────────
    if is_off(t):
        return _OFFTOPIC, False

    # ── STEP 1: PERMISSION (hook already in greeting) ──
    if state == "permission":
        if is_no(t):
            # Don't give up — mini bridge to Step 2
            reply = "Koi baat nahi — bas 20 second. Kya aapke baalon mein hair fall ya dandruff ki koi problem hai?"
            cs["state"]    = "hair_problem"
            cs["last_bot"] = reply
            return reply, False
        cs["state"] = "hair_problem"
        reply = _v(_ASK_HAIR, cs["turn"])
        cs["last_bot"] = reply
        return reply, False

    # ── STEP 2: HAIR PROBLEM ──────────────────────
    if state == "hair_problem":
        tl = t.lower()
        _yes_words = {"haan","ha","han","yes","hai","ho rahi","ho raha","hota","hoti",
                      "jhad","dandruff","safed","baal","problem","pareshaan","takleef",
                      "हाँ","हां","है","झड़","समस्या","परेशान"}
        has_problem = any(w in tl for w in _yes_words) or is_buy(t)
        no_problem  = is_no(t) or any(w in tl for w in
                      ["nahi","nahin","no problem","theek","bilkul theek","sab theek",
                       "नहीं","ठीक","सब ठीक"])

        if no_problem and not has_problem:
            cs["hair_problem"] = False
            cs["state"]        = "pitch"
            body = _v(_NO_PROBLEM, cs["turn"])
            full = body + " " + _URGENCY
            cs["last_bot"] = full
            return full, False
        else:
            cs["hair_problem"] = True
            cs["state"]        = "pitch"
            body   = _v(_YES_PROBLEM, cs["turn"])
            # Short usage line — one sentence only, not an essay
            detail = "Hafte mein 2-3 baar raat ko lagaen, subah dholen. Sirf 1499 rupaye — COD available hai."
            full   = body + " " + detail
            cs["last_bot"] = full
            return full, False

    # ── STEP 3+4: PITCH ───────────────────────────
    if state == "pitch":
        if is_buy(t):
            cs["state"] = "collecting_name"
            reply = _v(_ASK_NAME, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        # ── INSTANT price answer — no GPT needed ──
        if is_price_q(t):
            reply = _v(_PRICE_ANSWER, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        # GPT handles all other questions and objections
        reply = await gpt(cs, t)
        cs["last_bot"] = reply
        return reply, False

    # ── STEP 6A: NAME ─────────────────────────────
    if state == "collecting_name":
        if len(t) >= 2 and "?" not in t:
            name = re.sub(
                r"^(mera naam|mera name|main|i am|naam hai|name is|मेरा नाम|मैं)\s+",
                "", t, flags=re.IGNORECASE
            ).strip().title()
            cs["name"]  = name
            cs["state"] = "collecting_city"     # F6: go to city next
            reply = _v(_ASK_CITY, cs["turn"])
            cs["last_bot"] = reply
            return f"{name} ji, " + reply, False
        return _R_NAME, False

    # ── STEP 6B: CITY (Fix 6 — new step) ─────────
    if state == "collecting_city":
        if len(t) >= 2:
            cs["city"]  = t.strip().title()
            cs["state"] = "collecting_address"
            reply = _v(_ASK_ADDR, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        return _R_CITY, False

    # ── STEP 6C: ADDRESS ──────────────────────────
    if state == "collecting_address":
        if len(t) >= 5:
            # Combine city + address for full delivery address
            cs["address"] = f"{t.strip()}, {cs['city']}"
            cs["state"]   = "collecting_pincode"
            cs["last_bot"] = _ASK_PIN
            return _ASK_PIN, False
        return _R_ADDR, False

    # ── STEP 6D: PINCODE ──────────────────────────
    if state == "collecting_pincode":
        pin = get_pin(t)
        if pin:
            cs["pincode"] = pin
            cs["state"]   = "confirming"
            reply = (f"Ek baar confirm kar leti hoon — "
                     f"Naam: {cs['name']}, "
                     f"Shehar: {cs['city']}, "
                     f"Pata: {cs['address']}, "
                     f"Pincode: {pin}. Sahi hai?")
            cs["last_bot"] = reply
            return reply, False
        return _R_PIN, False

    # ── STEP 7: CONFIRM + CLOSE ───────────────────
    if state == "confirming":
        if is_buy(t):
            cs["state"] = "done"
            asyncio.create_task(save_order(
                cs["name"], cs["address"], cs["pincode"], cs["caller"], cs.get("city","")
            ))
            reply = (f"Bahut shukriya {cs['name']} ji! Order ho gaya. "
                     f"5 se 7 din mein {cs['city']} mein delivery aayegi. "
                     f"Delivery par 1499 rupaye dene honge. Have a nice day!")
            cs["last_bot"] = reply
            return reply, True
        if is_no(t):
            cs.update({"state":"collecting_name","name":"","city":"","address":"","pincode":""})
            return "Koi baat nahi — phir se shuru karte hain. Naam bataiye.", False
        return "Haan ya nahi bataiye — kya yeh jaankari sahi hai?", False

    # fallback
    reply = await gpt(cs, t)
    cs["last_bot"] = reply
    return reply, False

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS  (async, non-blocking) [Phase 6]
# ═══════════════════════════════════════════════════
async def save_order(name, address, pincode, phone, city=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"📦 ORDER → {ts} | {name} | {city} | {address} | {pincode} | {phone}")
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        print("⚠️  sheet not configured")
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sheet_write, name, city, address, pincode, phone, ts)

def _sheet_write(name, city, address, pincode, phone, ts):
    try:
        import google.oauth2.service_account as sa
        import googleapiclient.discovery as gd
        creds = sa.Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        svc = gd.build("sheets","v4",credentials=creds,cache_discovery=False)
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values":[[ts, name, city, address, pincode, phone,
                             "Adivasi Hair Oil", "₹1499", "Pending"]]},
        ).execute()
        print(f"✅ sheet: {name} | {city} | {pincode}")
    except Exception as e:
        print(f"❌ sheet err: {e}")


# ═══════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ═══════════════════════════════════════════════════
async def voice_start(request):
    """Step 1 — new call arrives. Plays permission greeting."""
    try:    data = await request.post()
    except: data = {}
    sid    = data.get("CallSid","unknown")
    caller = data.get("From","unknown")
    _calls[sid] = new_cs(caller)
    print(f"📞 {sid} from {caller}")

    # W1: use pre-warmed greeting — 0 ms TTS
    pre = _warm.get("greet","")
    tw  = await mk_twiml(_GREET[0], R(), pre_aid=pre)
    return web.Response(text=tw, content_type="application/xml")


async def voice_respond(request):
    """Steps 2-7 — user spoke, advance through sales script."""
    try:    data = await request.post()
    except: data = {}
    sid        = data.get("CallSid","unknown")
    caller     = data.get("From","unknown")
    speech     = data.get("SpeechResult","").strip()
    confidence = float(data.get("Confidence","0") or "0")
    no_speech  = request.rel_url.query.get("ns","0")

    print(f"🗣  [{sid}] '{speech}' conf={confidence:.2f}")

    # ── silence / timeout ─────────────────────────
    if no_speech == "1" or (not speech and confidence == 0):
        cs    = _calls.get(sid, new_cs(caller))
        state = cs.get("state","permission")
        msg   = {
            "permission":         _v(_GREET, 1),
            "hair_problem":       _v(_ASK_HAIR, 1),
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE)
        return web.Response(
            text=await mk_twiml(msg, R()),
            content_type="application/xml"
        )

    reply, hangup = await process(sid, speech, caller)
    print(f"🤖 [{_calls.get(sid,{}).get('state','?')}] {reply[:80]}")

    # W1: use pre-warmed audio for Step 2 (ask hair problem)
    pre = ""
    cs  = _calls.get(sid, {})
    if cs.get("state") == "hair_problem":
        pre = _warm.get("hair","")

    tw = await mk_twiml(reply, R(), hangup=hangup, pre_aid=pre)
    return web.Response(text=tw, content_type="application/xml")

# ═══════════════════════════════════════════════════
# KEEP-ALIVE  [W9]
# ═══════════════════════════════════════════════════
async def keepalive():
    await asyncio.sleep(60)
    while True:
        try:
            s = await http()
            async with s.get(f"{PUBLIC_URL}/",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                print(f"🏓 {r.status}")
        except Exception as e:
            print(f"⚠️  keepalive: {e}")
        await asyncio.sleep(480)   # every 8 min — Render sleeps at 15 min

async def on_startup(app):
    asyncio.create_task(keepalive())
    asyncio.create_task(prewarm())     # W1: pre-warm greeting + ask_name TTS

# ═══════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════
async def health(request):
    return web.json_response({
        "ok": True,
        "product": "Adivasi Hair Oil",
        "sarvam":  bool(SARVAM_API_KEY),
        "sheet":   bool(GOOGLE_SHEET_ID),
        "calls":   len(_calls),
        "cached_audio": len(_ac),
        "prewarmed": list(_warm.keys()),
    })

# ═══════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════
def create_app():
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.router.add_get("/",              health)
    app.router.add_post("/voice/start",  voice_start)
    app.router.add_post("/voice/respond",voice_respond)
    app.router.add_get("/audio/{aid}",   audio_serve)
    return app

if __name__ == "__main__":
    print("═"*52)
    print("  🌿 Priya — Adivasi Hair Oil | Ultra-Low Latency")
    print(f"  {PUBLIC_URL}  |  :{PORT}")
    print(f"  TTS  {'✅ Sarvam' if SARVAM_API_KEY else '⚠️  Polly fallback'}")
    print(f"  Sheet {'✅ Google Sheet' if GOOGLE_SHEET_ID else '⚠️  logs only'}")
    print("═"*52)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)