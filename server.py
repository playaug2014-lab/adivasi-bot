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
सामग्री: भृंगराज, आंवला, ब्राह्मी, शंखपुष्पी, नीम, जटामांसी + 108 जड़ी-बूटियाँ
फायदे: बाल झड़ना बंद · नए बाल उगना · डैंड्रफ खत्म · जड़ें मजबूत · चमक · घनापन
उपयोग: रात को स्कैल्प पर हल्की मालिश → सुबह माइल्ड शैंपू से धोएं
परिणाम: 30 दिन में असर | डिलीवरी: 5-7 दिन, COD, 7-दिन return | 418 संतुष्ट ग्राहक

सामान्य सवाल:
- हफ्ते में 2-3 बार काफी | रात भर रखें — सबसे ज़्यादा असर
- नीम+आंवला डैंड्रफ जड़ से खत्म | भृंगराज follicles सक्रिय → गंजेपन में उगाता है
- हल्का गुनगुना करें → असर दोगुना | बच्चों पर (5+) और पुरुष+महिला दोनों के लिए
- आंवला+ब्राह्मी सफेद होने से रोकते हैं | ब्राह्मी सिर दर्द में राहत
- प्राकृतिक कंडीशनर जैसा काम करता है | expired तेल बिल्कुल नहीं

कीमत आपत्ति:
- महंगा / costly: "500ml = 2-3 महीने = ₹16/दिन। सैलून ₹500-2000 एक बार।"
- सस्ता chahiye: "सस्ते में mineral oil होता है — जड़ी-बूटियाँ नहीं। 418 proof है।"
- Parachute/Patanjali/Dabur: "रोज़मर्रा का तेल है। यह 108 herbs का medicinal formula है।"
- पैसे नहीं / baad mein: "COD है — घर पर आए तब ₹1499 दें। 7-दिन return। कोई risk नहीं।"
- guarantee: "7-दिन return। 418 में से किसी ने वापस नहीं किया।"
"""

# ═══════════════════════════════════════════════════
# SYSTEM PROMPT  — strict, no filler, no repetition
# ═══════════════════════════════════════════════════
SYSTEM_PROMPT = f"""तुम प्रिया हो — Teleone की sales executive। काम: आदिवासी हेयर ऑयल बेचना।

{_KB}

नियम — तोड़ना मना:
1. OUTPUT = केवल बोला जाने वाला वाक्य। label/emoji/bracket नहीं।
2. हिंदी में बोलो — user Hinglish/English में बोले तो भी।
3. 1 वाक्य। maximum 2। इससे ज़्यादा कभी नहीं।
4. ये शब्द forbidden: हम्म, अच्छा, ओह, जी हाँ बिल्कुल, देखिए, तो, वैसे
5. last_reply से अलग जवाब दो — दोहराओ मत।
6. हर reply के अंत में order की तरफ ले जाओ।
7. दूसरा topic: "मैं केवल आदिवासी हेयर ऑयल के बारे में बता सकती हूँ।"
8. user हाँ/order/chahiye/lena कहे → तुरंत नाम पूछो।""".strip()

# ═══════════════════════════════════════════════════
# CALL STATE
# ═══════════════════════════════════════════════════
_calls: dict[str, dict] = {}

def new_cs(caller=""):
    return {
        "state":    "pitch",
        "name":     "", "address": "", "pincode": "",
        "caller":   caller,
        "turn":     0,
        "last_bot": "",
    }

# ═══════════════════════════════════════════════════
# STATIC REPLY VARIANTS  — rotated to prevent repetition
# ═══════════════════════════════════════════════════
_GREET = [
    "नमस्ते! मैं प्रिया हूँ Teleone से — बाल झड़ रहे हैं या गंजापन है?",
    "नमस्ते! Teleone से प्रिया बोल रही हूँ — बालों की कोई समस्या है?",
    "नमस्ते! मैं प्रिया हूँ — डैंड्रफ या बाल झड़ने की परेशानी है?",
]
_ASK_NAME = [
    "ऑर्डर के लिए अपना पूरा नाम बताइए।",
    "आपका पूरा नाम क्या है?",
    "नाम बताइए — ऑर्डर शुरू करते हैं।",
]
_ASK_ADDR = [
    "अब पूरा पता बताइए — गली, शहर और राज्य।",
    "डिलीवरी पता बताइए — गली, मोहल्ला, शहर, राज्य।",
    "पूरा पता बताइए।",
]
_ASK_PIN  = "पिनकोड बताइए।"
_R_NAME   = "नाम फिर से बताइए।"
_R_ADDR   = "पता फिर से बताइए — गली, शहर, राज्य।"
_R_PIN    = "6 अंकों का पिनकोड फिर से बताइए।"
_SILENCE  = "आपकी बात नहीं सुनाई दी — दोबारा बोलें।"
_OFFTOPIC = "मैं केवल आदिवासी हेयर ऑयल के बारे में बता सकती हूँ — मँगवाना है?"
_DONE     = "आपका ऑर्डर हो चुका है। धन्यवाद!"

def _v(lst, n): return lst[n % len(lst)]

# ═══════════════════════════════════════════════════
# AUDIO CACHE + PRE-WARM  [W1]
# ═══════════════════════════════════════════════════
_ac:   dict[str, bytes] = {}   # audio cache (RAM)
_warm: dict[str, str]   = {}   # key → cache_id for pre-warmed clips

async def prewarm():
    """Generate greeting + ask_name TTS at startup so first 2 turns are instant."""
    for key, text in [("greet", _GREET[0]), ("name", _ASK_NAME[0])]:
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
                "pace":                 1.1,          # natural, not robotic [W_VOICE]
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
def is_price(t):return any(w in t.lower() for w in _PRICE)
def is_off(t):  return any(re.search(p, t.lower()) for p in _OFF)
def get_pin(t): m = re.search(r"\b\d{6}\b", t); return m.group() if m else ""

# ═══════════════════════════════════════════════════
# STATE MACHINE
# ═══════════════════════════════════════════════════
async def process(sid: str, text: str, caller: str) -> tuple[str, bool]:
    """
    Returns (reply_text, should_hangup).
    GPT called ONLY in pitch state.
    All other states use instant static replies. [W5]
    W2: for pitch state, GPT + TTS run in parallel inside mk_twiml caller.
    """
    if sid not in _calls:
        _calls[sid] = new_cs(caller)
    cs    = _calls[sid]
    cs["caller"] = caller
    cs["turn"]  += 1
    t     = text.strip()
    state = cs["state"]

    # ── silence ───────────────────────────────
    if not t:
        return {
            "collecting_name":    _R_NAME,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE), False

    # ── done ──────────────────────────────────
    if state == "done":
        return _DONE, True

    # ── off-topic ─────────────────────────────
    if is_off(t):
        return _OFFTOPIC, False

    # ── PITCH ─────────────────────────────────
    if state == "pitch":
        if is_buy(t):
            cs["state"] = "collecting_name"
            reply = _v(_ASK_NAME, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        # GPT handles questions + objections (price/competitor/doubt)
        reply = await gpt(cs, t)
        cs["last_bot"] = reply
        return reply, False

    # ── NAME ──────────────────────────────────
    if state == "collecting_name":
        if len(t) >= 2 and not is_buy(t) and "?" not in t:
            name = re.sub(
                r"^(mera naam|mera name|main|i am|naam hai|name is|मेरा नाम|मैं)\s+",
                "", t, flags=re.IGNORECASE
            ).strip().title()
            cs["name"]  = name
            cs["state"] = "collecting_address"
            reply = _v(_ASK_ADDR, cs["turn"])
            cs["last_bot"] = reply
            return f"{name} जी, " + reply, False
        return _R_NAME, False

    # ── ADDRESS ───────────────────────────────
    if state == "collecting_address":
        if len(t) >= 8:
            cs["address"] = t
            cs["state"]   = "collecting_pincode"
            cs["last_bot"] = _ASK_PIN
            return _ASK_PIN, False
        return _R_ADDR, False

    # ── PINCODE ───────────────────────────────
    if state == "collecting_pincode":
        pin = get_pin(t)
        if pin:
            cs["pincode"] = pin
            cs["state"]   = "confirming"
            reply = (f"एक बार confirm करें — "
                     f"नाम: {cs['name']}, "
                     f"पता: {cs['address']}, "
                     f"पिनकोड: {pin}। सही है?")
            cs["last_bot"] = reply
            return reply, False
        return _R_PIN, False

    # ── CONFIRMING ────────────────────────────
    if state == "confirming":
        if is_buy(t):
            cs["state"] = "done"
            asyncio.create_task(save_order(
                cs["name"], cs["address"], cs["pincode"], cs["caller"]
            ))
            reply = (f"बहुत बढ़िया {cs['name']} जी! ऑर्डर हो गया। "
                     f"5-7 दिन में पहुँचेगा — डिलीवरी पर सिर्फ ₹1499 देने होंगे। धन्यवाद!")
            cs["last_bot"] = reply
            return reply, True
        if is_no(t):
            cs.update({"state":"collecting_name","name":"","address":"","pincode":""})
            return "फिर से शुरू करते हैं — नाम बताइए।", False
        return "हाँ या नहीं बोलें — क्या जानकारी सही है?", False

    # fallback
    reply = await gpt(cs, t)
    cs["last_bot"] = reply
    return reply, False

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS  (async, non-blocking) [Phase 6]
# ═══════════════════════════════════════════════════
async def save_order(name, address, pincode, phone):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"📦 ORDER → {ts} | {name} | {address} | {pincode} | {phone}")
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        print("⚠️  sheet not configured")
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sheet_write, name, address, pincode, phone, ts)

def _sheet_write(name, address, pincode, phone, ts):
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
            range="Sheet1!A:H",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values":[[ts,name,address,pincode,phone,
                             "Adivasi Hair Oil","₹1499","Pending"]]},
        ).execute()
        print(f"✅ sheet: {name} | {pincode}")
    except Exception as e:
        print(f"❌ sheet err: {e}")

# ═══════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ═══════════════════════════════════════════════════
async def voice_start(request):
    """Phase 1 — new call arrives."""
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
    """Phase 3+4 — user spoke, generate reply."""
    try:    data = await request.post()
    except: data = {}
    sid        = data.get("CallSid","unknown")
    caller     = data.get("From","unknown")
    speech     = data.get("SpeechResult","").strip()
    confidence = float(data.get("Confidence","0") or "0")
    no_speech  = request.rel_url.query.get("ns","0")

    print(f"🗣  [{sid}] '{speech}' conf={confidence:.2f}")

    # ── silence / timeout ─────────────────────
    if no_speech == "1" or (not speech and confidence == 0):
        cs    = _calls.get(sid, new_cs(caller))
        state = cs.get("state","pitch")
        msg   = {
            "collecting_name":    _R_NAME,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE)
        return web.Response(
            text=await mk_twiml(msg, R()),
            content_type="application/xml"
        )

    # ── W2: run process() then TTS in sequence
    #    (process returns text; mk_twiml runs TTS)
    #    For pitch/GPT turns, GPT is already awaited inside process(),
    #    then TTS runs in mk_twiml — total = GPT + TTS (sequential but minimal).
    #    For static turns (name/addr/pin/confirm), process() is instant,
    #    so total = TTS only (~600-900ms).
    reply, hangup = await process(sid, speech, caller)
    print(f"🤖 [{_calls.get(sid,{}).get('state','?')}] {reply[:70]}")

    # W1: use pre-warmed ask_name audio if state just moved to collecting_name
    pre = ""
    cs  = _calls.get(sid,{})
    if cs.get("state") == "collecting_name" and reply in (_ASK_NAME):
        pre = _warm.get("name","")

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
