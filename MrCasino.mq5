//+------------------------------------------------------------------+
//|                                                    MrCasino.mq5 |
//|   Советник MT5 по стратегии Mr. Casino (Flashcards), золото      |
//|   v2: ближние зоны, BUY и SELL, триггер D, подробный лог         |
//|   Реализация "ТЗ v2: робот MT5 Mr. Casino - ближние зоны,        |
//|   BUY и SELL". Разделы ТЗ помечены как [§N].                     |
//|                                                                  |
//|   Единицы: 1 пункт = $1 цены золота (пороги в ценовых единицах). |
//|   Внутри паттерны и зоны считаются для BUY ("бычья логика");     |
//|   для SELL цены зеркалятся (аксессоры Ov/Hv/Lv/Cv с d = -1).     |
//+------------------------------------------------------------------+
#property copyright "Consigliere"
#property version   "2.20"
#property description "Mr. Casino Flashcards v2: OB/FU/ликвидность -> ближние зоны -> триггеры A-D"

#include <Trade\Trade.mqh>

enum ENUM_RISK_MODE { RISK_FIXED = 0, RISK_PERCENT = 1, RISK_HALF = 2, RISK_FULL = 3 };
enum ENUM_MIN_FU    { FU_CLASS_A = 3, FU_CLASS_B = 2, FU_CLASS_C = 1 };

//--- входные параметры [§11]
input group "Символ и метка"
input string          InpSymbol         = "GOLD#";      // Символ золота у XM
input long            InpMagic          = 777001;       // Magic Number

input group "Риск и лот [§2]"
input ENUM_RISK_MODE  InpRiskMode       = RISK_FIXED;   // Режим риска
input double          InpFixedLot       = 0.01;         // Лот для FIXED
input double          InpRiskPercent    = 10.0;         // % баланса для PERCENT
input double          InpMarginReserve  = 30.0;         // Запас свободной маржи, %

input group "Направления и баллы [§4, §6.2]"
input bool            InpTradeBuy       = true;         // Разрешить BUY
input bool            InpTradeSell      = true;         // Разрешить SELL
input bool            InpAllowCounter   = true;         // Сделки COUNTER (против D1 и H4)
input int             InpMinScoreWith   = 2;            // Мин. балл зоны: WITH
input int             InpMinScoreMixed  = 3;            // Мин. балл зоны: MIXED / NEUTRAL
input int             InpMinScoreCounter= 5;            // Мин. балл зоны: COUNTER
input int             InpRepeatM15Bars  = 2;            // Пауза после стопа в ту же сторону, свечей M15

input group "Зоны [§6]"
input double          InpZoneRadius     = 60.0;         // Радиус поиска зон от цены, пунктов
input int             InpMaxZonesSide   = 3;            // Зон на сторону
input bool            InpUseM15OB       = true;         // OB на M15
input double          InpOBWickPct      = 45.0;         // Макс. сумма теней свечи OB, %
input int             InpOBMoveBars     = 6;            // Свечей на уход цены от OB
input bool            InpOBNeedFU       = false;        // FU после OB обязателен
input double          InpFUZoneMinWick  = 3.0;          // Мин. тень FU для зоны из тени
input double          InpSweepZone      = 6.0;          // Зона за уровнем ликвидности
input double          InpEqTol          = 1.0;          // Допуск равных хаёв/лоёв
input double          InpZoneMax        = 10.0;         // Макс. ширина зоны
input double          InpZoneMin        = 3.0;          // Мин. ширина зоны
input double          InpConfirmAway    = 10.0;         // Отход от зоны для CONFIRMED
input double          InpInvalidBeyond  = 2.0;          // Закрытие M15 за дальним краем для INVALID
input int             InpZoneLifeHours  = 48;           // Срок жизни зоны, часов
input int             InpZoneLifeM15    = 24;           // Срок жизни зоны M15, часов

input group "Паттерны [§5]"
input double          InpDojiBodyPct    = 25.0;         // Тело Doji, % диапазона
input double          InpWickPct        = 40.0;         // Тень у FU / сигнала D, %
input double          InpLqMin          = 0.3;          // Прокол экстремума, пунктов
input ENUM_MIN_FU     InpMinFUClass     = FU_CLASS_B;   // Мин. класс FU для триггера A
input double          InpHCSTol         = 1.0;          // Допуск HCS, пунктов
input double          InpPierceMax      = 6.0;          // Допуск прокола за край зоны, пунктов
input int             InpTrigLookback   = 3;            // Свечей M5 на поиск прокола (A, D)

input group "Триггеры [§7]"
input bool            InpUseTrigA       = true;         // A: Manipulation Cycle
input bool            InpUseTrigB       = true;         // B: HCS
input bool            InpUseTrigC       = true;         // C: Negation
input bool            InpUseTrigD       = true;         // D: снятие ликвидности
input bool            InpDirectEntry    = false;        // Прямой вход: BIW + Doji
input bool            InpUseTrigE       = true;         // E: отбой от зоны (касание + свеча разворота)
input double          InpTouchWickPct   = 25.0;         // E: мин. тень в сторону зоны, %

input group "Стоп и тейк [§8]"
input double          InpMaxSL          = 12.0;         // Макс. стоп, пунктов
input double          InpMinSL          = 3.0;          // Мин. стоп, пунктов
input double          InpSLBuffer       = 0.5;          // Буфер стопа сверх спреда
input double          InpTP             = 40.0;         // Тейк, пунктов
input double          InpMinRR          = 1.5;          // Мин. тейк/стоп
input double          InpBEAt           = 15.0;         // Безубыток: N пунктов или 50% пути до тейка
input double          InpBEOffset       = 0.5;          // Стоп в безубытке = вход + N
input bool            InpPartial        = false;        // 50% на первой LAL (лот от 0.02)
input int             InpFridayMin      = 30;           // Закрыть позиции за N минут до закрытия в пятницу

input group "Фильтры и лимиты [§9]"
input int             InpMaxTradesDay   = 3;            // Сделок в день
input int             InpMaxCounterDay  = 1;            // Из них COUNTER
input double          InpDailyTarget    = 40.0;         // Дневная цель, пунктов
input int             InpMaxLossStreak  = 2;            // Стопов подряд до паузы
input double          InpMaxSpread      = 0.5;          // Макс. спред
input double          InpMaxSlip        = 0.3;          // Проскальзывание (deviation)
input string          InpSessionStart   = "10:00";      // Начало входов (Бишкек)
input string          InpSessionEnd     = "23:00";      // Конец входов (Бишкек)
input string          InpLondonStart    = "13:00";      // Метка LONDON с (Бишкек)
input string          InpNYStart        = "18:00";      // Метка NY с (Бишкек)
input int             InpUtcOffset      = 6;            // Часовой пояс Бишкека относительно UTC
input int             InpServerUtc      = -99;          // UTC-сдвиг сервера в часах; -99 = авто по TimeGMT
input string          InpNewsTimes      = "";           // Новости "ГГГГ.ММ.ДД ЧЧ:ММ;..." (Бишкек)
input int             InpNewsMinutes    = 30;           // Окно вокруг новости, минут

input group "Лог, график, уведомления [§10]"
input int             InpLogLevel       = 1;            // 0 сделки, 1 зоны и пропуски, 2 подробно
input bool            InpPush           = true;         // Push в MT5 на iPhone
input string          InpTgToken        = "";           // Telegram bot token
input string          InpTgChatId       = "";           // Telegram chat id
input bool            InpDraw           = true;         // Рисовать зоны на графике
input string          InpLogFile        = "MrCasino_v2_trades.csv"; // CSV-лог (MQL5/Files)

//--- константы
#define ZONE_NEW        0
#define ZONE_CONFIRMED  1
#define ZONE_USED       2
#define ZONE_INVALID    3
#define T_WITH          0
#define T_MIXED         1
#define T_COUNTER       2
#define T_NEUTRAL       3
#define OBJ_PREFIX      "MC_"

//--- структуры
struct SCand                       // кандидат в зону, зеркальные координаты
  {
   int               src;          // 1 OB, 2 тень FU, 3 ликвидность
   ENUM_TIMEFRAMES   tf;
   double            lo, hi;
   datetime          t0;           // конец свечи-источника
   int               pts;
   bool              fuAfter;
   double            lvl;          // зеркальный уровень ликвидности
   string            label;
  };

struct SZone
  {
   int               dir;          // +1 BUY, -1 SELL
   double            lo, hi;       // торговая зона, реальные цены
   double            ulo, uhi;     // объединение источников (идентификация)
   datetime          born;         // конец самого раннего источника
   datetime          newest;       // конец самого свежего источника
   ENUM_TIMEFRAMES   topTF;
   int               status;
   bool              htf;
   double            lvl4;
   int               srcScore;
   int               score;
   bool              hasHCS;
   bool              tradable;
   bool              notified;
   string            srcs;
   int               nLvl;
   double            lvl[6];       // реальные цены снятой ликвидности
   string            lastSkip;
   datetime          lastSkipT;
   int               updId;        // номер пересчёта, в котором зона обновлена
  };

struct SSig
  {
   bool              ok;
   string            trig;
   int               cls;
   bool              strong;
   int               pts;          // вклад триггера и класса FU в силу сигнала
   double            ext;          // самая дальняя точка манипуляции (зеркально)
   string            mark;
   datetime          t;
  };

struct SDay
  {
   int               trades;
   int               counter;
   double            pts;
   int               lossStreak;
   datetime          lastLossBuy;
   datetime          lastLossSell;
  };

//--- глобальные переменные
CTrade     g_trade;
string     g_sym;
int        g_digits;
double     g_point;
SZone      g_zones[];
int        g_d1         = 0;
int        g_h4         = 0;
double     g_lv[8];                // [0..3] BUY, [4..7] SELL
bool       g_hasLv[2];
datetime   g_lastM5     = 0;
datetime   g_lastM15    = 0;
datetime   g_lastH1     = 0;
datetime   g_news[];
int        g_sessStart  = 0;
int        g_sessEnd    = 0;
int        g_londonMin  = 0;
int        g_nyMin      = 0;
SDay       g_day;
bool       g_isTester   = false;
int        g_markIdx    = 0;
string     g_lastSkip   = "-";
double     g_liq[];                // неснятые уровни ликвидности, реальные цены
int        g_liqDir[];
string     g_lk[];
int        g_cntDayKey  = -1;
int        g_cntToday   = 0;
int        g_rebuildId  = 0;

// открытая позиция робота
long       g_pId        = 0;
int        g_pDir       = 0;
string     g_pTrig      = "";
int        g_pCls       = 0;
string     g_pStrength  = "";
string     g_pType      = "";
int        g_pScore     = 0;
string     g_pSrcs      = "";
string     g_pSess      = "";
double     g_pEntry     = 0;
double     g_pSL        = 0;
double     g_pTP        = 0;
double     g_pLot       = 0;
double     g_pZoneW     = 0;
double     g_pZLo       = 0;
double     g_pZHi       = 0;
double     g_pLal       = 0;
double     g_pBEAt      = 0;
bool       g_beDone     = false;
bool       g_partDone   = false;
string     g_exitTag    = "";
int        g_closeRetry = 0;

//+------------------------------------------------------------------+
//| Лог и уведомления [§10]                                          |
//+------------------------------------------------------------------+
void Lg(int lvl, string s)
  {
   if(lvl <= InpLogLevel) Print(s);
  }

bool LogOnce(string k)
  {
   for(int i = ArraySize(g_lk) - 1; i >= 0; i--)
      if(g_lk[i] == k) return false;
   int n = ArraySize(g_lk);
   if(n >= 3000) { ArrayResize(g_lk, 0); n = 0; }
   ArrayResize(g_lk, n + 1);
   g_lk[n] = k;
   return true;
  }

string UrlEncode(string s)
  {
   uchar b[];
   int n = StringToCharArray(s, b, 0, -1, CP_UTF8);
   string out = "";
   for(int i = 0; i < n - 1; i++)
     {
      uchar c = b[i];
      if((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         c == '-' || c == '_' || c == '.' || c == '~')
         out += CharToString(c);
      else
         out += StringFormat("%%%02X", c);
     }
   return out;
  }

void SendTG(string text)
  {
   if(g_isTester || InpTgToken == "" || InpTgChatId == "") return;
   string url = "https://api.telegram.org/bot" + InpTgToken + "/sendMessage";
   string body = "chat_id=" + UrlEncode(InpTgChatId) + "&text=" + UrlEncode(text);
   uchar data[], res[];
   int n = StringToCharArray(body, data, 0, -1, CP_UTF8);
   if(n > 0) ArrayResize(data, n - 1);
   string hdr = "Content-Type: application/x-www-form-urlencoded\r\n", rh;
   ResetLastError();
   int code = WebRequest("POST", url, hdr, 5000, data, res, rh);
   if(code != 200)
      Print("Telegram: ошибка отправки, код ", code, ", err ", GetLastError());
  }

void Notify(string text)
  {
   Print("[NOTIFY] ", text);
   SendTG(text);
   if(InpPush && !g_isTester)
     {
      string p = text;
      StringReplace(p, "\n", " | ");
      if(StringLen(p) > 250) p = StringSubstr(p, 0, 250);
      if(!SendNotification(p)) Print("Push: ошибка ", GetLastError());
     }
  }

void LogTrade(string line)
  {
   int h = FileOpen(InpLogFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
     {
      Print("CSV: не открыть файл ", InpLogFile, ", err ", GetLastError());
      return;
     }
   if(FileSize(h) == 0)
      FileWriteString(h, "date,side,type,trigger,fu_class,score,zone_sources,zone_width,session,entry,sl,tp,lot,result_pts,result_usd,exit,strength\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| Зеркальные аксессоры: для SELL цены отражаются                   |
//+------------------------------------------------------------------+
double Ov(const MqlRates &r[], int i, int d) { return d > 0 ? r[i].open  : -r[i].open;  }
double Cv(const MqlRates &r[], int i, int d) { return d > 0 ? r[i].close : -r[i].close; }
double Hv(const MqlRates &r[], int i, int d) { return d > 0 ? r[i].high  : -r[i].low;   }
double Lv(const MqlRates &r[], int i, int d) { return d > 0 ? r[i].low   : -r[i].high;  }
double BTop(const MqlRates &r[], int i, int d) { return MathMax(Ov(r, i, d), Cv(r, i, d)); }
double BBot(const MqlRates &r[], int i, int d) { return MathMin(Ov(r, i, d), Cv(r, i, d)); }
double Rng(const MqlRates &r[], int i, int d)  { return Hv(r, i, d) - Lv(r, i, d); }

double ZLo(const SZone &z) { return z.dir > 0 ? z.lo : -z.hi; }
double ZHi(const SZone &z) { return z.dir > 0 ? z.hi : -z.lo; }

int Load(ENUM_TIMEFRAMES tf, int n, MqlRates &r[])
  {
   ArraySetAsSeries(r, true);
   return CopyRates(g_sym, tf, 0, n, r);
  }

string TFName(ENUM_TIMEFRAMES tf) { return StringSubstr(EnumToString(tf), 7); }
string ClsName(int c) { return c == 3 ? "A" : (c == 2 ? "B" : (c == 1 ? "C" : "-")); }
string DirName(int d) { return d > 0 ? "BUY" : "SELL"; }
string TypeName(int t) { return t == T_WITH ? "WITH" : (t == T_MIXED ? "MIXED" : (t == T_COUNTER ? "COUNTER" : "NEUTRAL")); }
string BiasName(int b) { return b > 0 ? "BUY" : (b < 0 ? "SELL" : "NONE"); }
string StatusName(int s) { return s == ZONE_NEW ? "NEW" : (s == ZONE_CONFIRMED ? "CONFIRMED" : (s == ZONE_USED ? "USED" : "INVALID")); }
string Stars(int p) { return p >= 7 ? "★★★" : (p >= 5 ? "★★" : "★"); }
string Px(double v) { return DoubleToString(v, g_digits); }

//+------------------------------------------------------------------+
//| Паттерны [§5]                                                    |
//+------------------------------------------------------------------+
bool IsDoji(const MqlRates &r[], int i)
  {
   if(i < 0 || i >= ArraySize(r)) return false;
   double rg = r[i].high - r[i].low;
   if(rg <= 0) return false;
   return MathAbs(r[i].close - r[i].open) <= InpDojiBodyPct / 100.0 * rg;
  }

bool IsFU(const MqlRates &r[], int i, int d)
  {
   if(i < 0 || i + 1 >= ArraySize(r)) return false;
   return Lv(r, i, d) < Lv(r, i + 1, d) - InpLqMin && Cv(r, i, d) > Lv(r, i + 1, d);
  }

bool IsEngulf(const MqlRates &r[], int i, int d)
  {
   return Cv(r, i, d) > BTop(r, i + 1, d) && Ov(r, i, d) <= BBot(r, i + 1, d);
  }

bool IsBodyCover(const MqlRates &r[], int i, int d)
  {
   double b1 = BTop(r, i + 1, d) - BBot(r, i + 1, d);
   if(b1 <= 0) return false;
   double ov = MathMin(BTop(r, i, d), BTop(r, i + 1, d)) - MathMax(BBot(r, i, d), BBot(r, i + 1, d));
   return ov >= 0.5 * b1;
  }

bool IsLowerWick(const MqlRates &r[], int i, int d)
  {
   double rg = Rng(r, i, d);
   if(rg <= 0) return false;
   return (BBot(r, i, d) - Lv(r, i, d)) >= InpWickPct / 100.0 * rg;
  }

bool IsBIW(const MqlRates &r[], int i, int d)
  {
   return BBot(r, i, d) >= Lv(r, i + 1, d) && BTop(r, i, d) <= BBot(r, i + 1, d);
  }

// Класс FU: 3=A (SFU), 2=B, 1=C, 0=не FU
int FUClass(const MqlRates &r[], int i, int d)
  {
   if(!IsFU(r, i, d)) return 0;
   bool bull = Cv(r, i, d) > Ov(r, i, d);
   if(IsBIW(r, i, d) && bull) return 3;
   if(IsEngulf(r, i, d)) return 3;
   if(IsBodyCover(r, i, d) && IsLowerWick(r, i, d)) return 2;
   return 1;
  }

// 0 нет, 1 обычный Negation, 2 Strong Negation. Сигнальная свеча i, отрицаемая i+1
int Negation(const MqlRates &r[], int i, int d)
  {
   if(i < 0 || i + 2 >= ArraySize(r)) return 0;
   if(!(Cv(r, i + 1, d) < Ov(r, i + 1, d))) return 0;
   double rg1 = Rng(r, i + 1, d);
   if(rg1 <= 0) return 0;
   if(BBot(r, i + 1, d) - Lv(r, i + 1, d) > 0.15 * rg1) return 0;
   if(!(Cv(r, i + 1, d) < Lv(r, i + 2, d))) return 0;
   if(!(Cv(r, i, d) > Ov(r, i, d))) return 0;
   if(!(Lv(r, i, d) < Lv(r, i + 1, d))) return 0;
   if(Cv(r, i, d) > Hv(r, i + 1, d)) return 2;
   if(Cv(r, i, d) > BTop(r, i + 1, d)) return 1;
   return 0;
  }

// Swing low: ниже двух свечей слева и двух справа (i >= 3: слева новее)
bool IsSwingLow(const MqlRates &r[], int i, int d)
  {
   if(i < 3 || i + 2 >= ArraySize(r)) return false;
   double L = Lv(r, i, d);
   return L < Lv(r, i - 1, d) && L < Lv(r, i - 2, d) && L < Lv(r, i + 1, d) && L < Lv(r, i + 2, d);
  }

//+------------------------------------------------------------------+
//| Время [§3, §9]                                                   |
//+------------------------------------------------------------------+
int GmtOffset()
  {
   if(InpServerUtc != -99) return InpServerUtc * 3600;
   double off = (double)(TimeCurrent() - TimeGMT());
   return (int)(MathRound(off / 900.0) * 900.0);
  }
datetime ToLocal(datetime srv)   { return srv - GmtOffset() + InpUtcOffset * 3600; }
datetime FromLocal(datetime loc) { return loc + GmtOffset() - InpUtcOffset * 3600; }
datetime LocalNow()
  {
   if(InpServerUtc != -99) return ToLocal(TimeCurrent());
   return TimeGMT() + InpUtcOffset * 3600;     // UTC + 6, не время сервера
  }

datetime DayStartSrv()
  {
   datetime l = LocalNow();
   return FromLocal(l - (l % 86400));
  }

int ParseHM(string s)
  {
   string p[];
   if(StringSplit(s, ':', p) < 2) return 0;
   return (int)StringToInteger(p[0]) * 60 + (int)StringToInteger(p[1]);
  }

int LocalMinutes() { return (int)((LocalNow() % 86400) / 60); }

bool InSession()
  {
   int mins = LocalMinutes();
   if(g_sessStart <= g_sessEnd) return mins >= g_sessStart && mins < g_sessEnd;
   return mins >= g_sessStart || mins < g_sessEnd;
  }

string SessionName()
  {
   int m = LocalMinutes();
   if(m < g_londonMin) return "ASIA";
   if(m < g_nyMin) return "LONDON";
   return "NY";
  }

string LocalHM()
  {
   datetime l = LocalNow();
   return StringFormat("%02d:%02d", (int)((l % 86400) / 3600), (int)((l % 3600) / 60));
  }

void ParseNews()
  {
   ArrayResize(g_news, 0);
   string s = InpNewsTimes;
   StringReplace(s, ",", ";");
   StringReplace(s, "\n", ";");
   string p[];
   int n = StringSplit(s, ';', p);
   for(int i = 0; i < n; i++)
     {
      StringTrimLeft(p[i]);
      StringTrimRight(p[i]);
      if(StringLen(p[i]) < 10) continue;
      datetime t = StringToTime(p[i]);
      if(t <= 0) continue;
      int k = ArraySize(g_news);
      ArrayResize(g_news, k + 1);
      g_news[k] = FromLocal(t);
     }
  }

bool NewsBlock()
  {
   datetime now = TimeCurrent();
   for(int i = 0; i < ArraySize(g_news); i++)
      if(MathAbs((double)(now - g_news[i])) <= InpNewsMinutes * 60.0) return true;
   return false;
  }

bool FridayCloseTime()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   if(t.day_of_week != 5) return false;
   datetime from, to;
   int maxTo = 0;
   for(uint k = 0; SymbolInfoSessionTrade(g_sym, FRIDAY, k, from, to); k++)
      maxTo = MathMax(maxTo, (int)to);
   if(maxTo == 0) maxTo = 86400;
   int now = t.hour * 3600 + t.min * 60 + t.sec;
   return now >= maxTo - InpFridayMin * 60;
  }

//+------------------------------------------------------------------+
//| Bias и тип сделки [§4]                                           |
//+------------------------------------------------------------------+
int CalcBias(ENUM_TIMEFRAMES tf)
  {
   MqlRates r[];
   if(Load(tf, 40, r) < 30) return 0;
   for(int i = 1; i <= 20; i++)
     {
      int nb = Negation(r, i, 1), ns = Negation(r, i, -1);
      if(nb > 0 && ns == 0) return 1;
      if(ns > 0 && nb == 0) return -1;
      bool fb = IsFU(r, i, 1), fs = IsFU(r, i, -1);
      if(fb && !fs) return 1;
      if(fs && !fb) return -1;
      if(fb && fs) return r[i].close > r[i].open ? 1 : -1;
     }
   return 0;
  }

void UpdateBias()
  {
   g_d1 = CalcBias(PERIOD_D1);
   g_h4 = CalcBias(PERIOD_H4);
  }

int TradeType(int d)
  {
   if(g_d1 == 0) return T_NEUTRAL;
   if(d == g_d1 && d == g_h4) return T_WITH;
   if(d == g_d1 || d == g_h4) return T_MIXED;
   return T_COUNTER;
  }

int MinScore(int tp)
  {
   if(tp == T_WITH) return InpMinScoreWith;
   if(tp == T_COUNTER) return InpMinScoreCounter;
   return InpMinScoreMixed;
  }

//+------------------------------------------------------------------+
//| HTF Re-test Levels на H4                                         |
//+------------------------------------------------------------------+
void FindHTFLevels(int d)
  {
   int base = d > 0 ? 0 : 4, hi = d > 0 ? 0 : 1;
   g_hasLv[hi] = false;
   MqlRates r[];
   int n = Load(PERIOD_H4, 60, r);
   if(n < 20) return;
   for(int k = 1; k <= 40 && k + 1 < n; k++)
     {
      if(!IsDoji(r, k + 1)) continue;
      double rgD = r[k + 1].high - r[k + 1].low;
      double rgB = r[k].high - r[k].low;
      if(rgB < 1.5 * rgD) continue;
      if(BBot(r, k, d) - Lv(r, k, d) < InpWickPct / 100.0 * rgB) continue;
      double m1 = Hv(r, k + 1, d), m3 = Lv(r, k + 1, d);
      g_lv[base]     = d * m1;
      g_lv[base + 1] = d * (m1 + m3) / 2.0;
      g_lv[base + 2] = d * m3;
      g_lv[base + 3] = d * (Lv(r, k, d) + BBot(r, k, d)) / 2.0;
      g_hasLv[hi] = true;
      return;
     }
  }

bool HtfMatch(int d, double lo, double hi, double &lvl4)
  {
   int base = d > 0 ? 0 : 4, h = d > 0 ? 0 : 1;
   lvl4 = 0;
   if(!g_hasLv[h]) return false;
   lvl4 = g_lv[base + 3];
   for(int k = 0; k < 4; k++)
      if(g_lv[base + k] >= lo - 2.0 && g_lv[base + k] <= hi + 2.0) return true;
   return false;
  }

//+------------------------------------------------------------------+
//| FVG и ликвидность [§5, §7, §8]                                   |
//+------------------------------------------------------------------+
// Реальная зона целиком внутри незакрытого FVG M5 шириной 3+ пункта
bool ZoneInFVG(double lo, double hi)
  {
   MqlRates r[];
   int n = Load(PERIOD_M5, 150, r);
   for(int k = 3; k < n - 1; k++)
     {
      double bot = r[k + 1].high, top = r[k - 1].low;           // бычий
      if(top - bot >= 3.0)
        {
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(r[q].low <= bot) { filled = true; break; }
         if(!filled && lo >= bot && hi <= top) return true;
        }
      bot = r[k - 1].high; top = r[k + 1].low;                  // медвежий
      if(top - bot >= 3.0)
        {
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(r[q].high >= top) { filled = true; break; }
         if(!filled && lo >= bot && hi <= top) return true;
        }
     }
   return false;
  }

// Сигнальная свеча M5 внутри незакрытого FVG 3+ пункта
bool InsideFVG(const MqlRates &r[])
  {
   int n = ArraySize(r);
   double c = r[1].close;
   for(int k = 3; k < MathMin(n - 1, 45); k++)
     {
      double bot = r[k + 1].high, top = r[k - 1].low;
      if(top - bot >= 3.0)
        {
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(r[q].low <= bot) { filled = true; break; }
         if(!filled && c >= bot && c <= top) return true;
        }
      bot = r[k - 1].high; top = r[k + 1].low;
      if(top - bot >= 3.0)
        {
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(r[q].high >= top) { filled = true; break; }
         if(!filled && c >= bot && c <= top) return true;
        }
     }
   return false;
  }

// Ближайшая Major ликвидность в сторону сделки (Doji, ровные уровни, верх FU, FVG); 0 если нет
double FindMajorLiq(int d, double entry)
  {
   double e = d * entry + 0.5;
   double best = DBL_MAX;
   ENUM_TIMEFRAMES tfs[3] = {PERIOD_M15, PERIOD_H1, PERIOD_H4};
   int cnts[3] = {200, 120, 60};
   for(int t = 0; t < 3; t++)
     {
      MqlRates r[];
      int n = Load(tfs[t], cnts[t], r);
      if(n < 10) continue;
      for(int i = 1; i < n - 1; i++)
        {
         double lvl = Hv(r, i, d);
         bool swept = false;
         for(int k = 1; k < i; k++)
            if(Hv(r, k, d) > lvl) { swept = true; break; }
         if(swept) continue;
         if(IsDoji(r, i) && !IsFU(r, i, 1) && !IsFU(r, i, -1))
            if(lvl > e && lvl < best) best = lvl;
         if(i >= 2 && Hv(r, i, d) > Hv(r, i - 1, d) && Hv(r, i, d) > Hv(r, i + 1, d))
            for(int j = i + 2; j < n - 1; j++)
              {
               if(!(Hv(r, j, d) > Hv(r, j - 1, d) && Hv(r, j, d) > Hv(r, j + 1, d))) continue;
               if(MathAbs(Hv(r, j, d) - lvl) <= InpEqTol)
                 {
                  if(lvl > e && lvl < best) best = lvl;
                  break;
                 }
              }
         if(tfs[t] != PERIOD_M15 && IsFU(r, i, d))
            if(lvl > e && lvl < best) best = lvl;
        }
     }
   ENUM_TIMEFRAMES ft[3] = {PERIOD_M5, PERIOD_M15, PERIOD_H1};
   for(int t = 0; t < 3; t++)
     {
      MqlRates r[];
      int n = Load(ft[t], 150, r);
      for(int k = 2; k < n - 1; k++)
        {
         double bottom = Hv(r, k - 1, d), topg = Lv(r, k + 1, d);
         if(topg - bottom < 1.0) continue;
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(Hv(r, q, d) >= topg) { filled = true; break; }
         if(filled) continue;
         if(bottom > e && bottom < best) best = bottom;
        }
     }
   return best == DBL_MAX ? 0.0 : d * best;
  }

// Ближайшее препятствие в сторону сделки: зона CONFIRMED противоположной стороны или Major-ликвидность.
// Возвращает расстояние в пунктах (0 если нет), в src - что это.
double NearestObstacle(int d, double entry, double major, string &src)
  {
   double best = 0;
   src = "";
   if(major > 0)
     {
      best = d * (major - entry);
      src = "ликвидность " + Px(major);
     }
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dir != -d || g_zones[i].status != ZONE_CONFIRMED) continue;
      double edge = d > 0 ? g_zones[i].lo : g_zones[i].hi;
      double dist = d * (edge - entry);
      if(dist <= 0) continue;
      if(best <= 0 || dist < best)
        {
         best = dist;
         src = StringFormat("зона %s %s–%s", DirName(-d), Px(g_zones[i].lo), Px(g_zones[i].hi));
        }
     }
   return best;
  }

//+------------------------------------------------------------------+
//| Зоны: общее [§6]                                                 |
//+------------------------------------------------------------------+
string ZoneTag(const SZone &z)
  {
   return StringFormat("%s %s–%s", DirName(z.dir), Px(z.lo), Px(z.hi));
  }

string ZoneLine(const SZone &z)
  {
   return StringFormat("%s | %s | балл %d", ZoneTag(z), z.srcs, z.score);
  }

void SetStatus(SZone &z, int ns, string why = "")
  {
   if(z.status == ns) return;
   string old = StatusName(z.status);
   z.status = ns;
   Lg(1, ZoneLine(z) + " | " + old + "→" + StatusName(ns) + (why != "" ? " (" + why + ")" : ""));
  }

long LifeSec(ENUM_TIMEFRAMES tf)
  {
   return tf == PERIOD_M15 ? (long)InpZoneLifeM15 * 3600 : (long)InpZoneLifeHours * 3600;
  }

// Зона рядом с ценой: SELL выше цены или на ней, BUY ниже или на ней, не дальше радиуса
bool NearPrice(int d, double lo, double hi)    // зеркальные lo/hi
  {
   double pm = d * SymbolInfoDouble(g_sym, SYMBOL_BID);
   return lo <= pm && (pm - hi) <= InpZoneRadius;
  }

double ZoneDist(const SZone &z)
  {
   double pm = z.dir * SymbolInfoDouble(g_sym, SYMBOL_BID);
   if(pm > ZHi(z)) return pm - ZHi(z);
   if(pm < ZLo(z)) return ZLo(z) - pm;
   return 0;
  }

bool ZoneInRadius(const SZone &z)
  {
   return NearPrice(z.dir, ZLo(z), ZHi(z));
  }

// Status NEW -> CONFIRMED: после образования цена ушла от зоны на InpConfirmAway+ [§6.3]
void AdvanceNew(SZone &z, const MqlRates &r[], int j)
  {
   if(z.status != ZONE_NEW) return;
   if(Hv(r, j, z.dir) >= ZHi(z) + InpConfirmAway)
      SetStatus(z, ZONE_CONFIRMED, "цена ушла на " + DoubleToString(InpConfirmAway, 0) + "+ пунктов");
  }

void CheckInvalidM15(SZone &z, const MqlRates &m[], int j)
  {
   if(z.status > ZONE_CONFIRMED) return;
   int d = z.dir;
   double c = Cv(m, j, d);
   if(c < ZLo(z) - InpInvalidBeyond) { SetStatus(z, ZONE_INVALID, "M15 закрылась за дальним краем"); return; }
   if(z.htf && c < d * z.lvl4) SetStatus(z, ZONE_INVALID, "пробит Level #4");
  }

// Статус по истории свечей от момента образования зоны (исправление v1)
void ReplayZone(SZone &z)
  {
   z.status = ZONE_NEW;
   MqlRates m5[], m15[];
   int sh5 = iBarShift(g_sym, PERIOD_M5, z.born);
   int n5 = Load(PERIOD_M5, MathMin(MathMax(sh5 + 2, 5), 900), m5);
   for(int j = n5 - 1; j >= 1; j--)
      if(m5[j].time >= z.born && z.status == ZONE_NEW) AdvanceNew(z, m5, j);
   int sh15 = iBarShift(g_sym, PERIOD_M15, z.born);
   int n15 = Load(PERIOD_M15, MathMin(MathMax(sh15 + 2, 5), 400), m15);
   for(int j = n15 - 1; j >= 1; j--)
      if(m15[j].time >= z.born) CheckInvalidM15(z, m15, j);
   if(z.status <= ZONE_CONFIRMED && TimeCurrent() - z.newest > LifeSec(z.topTF))
      SetStatus(z, ZONE_INVALID, "возраст");
  }

void ZoneNotify(SZone &z)
  {
   if(z.status != ZONE_CONFIRMED || z.notified) return;
   z.notified = true;
   Notify(StringFormat("%s: зона CONFIRMED\n%s–%s (%.1f pt) · балл %d\n%s",
                       DirName(z.dir), Px(z.lo), Px(z.hi), z.hi - z.lo, z.score, z.srcs));
  }

// Участок региона, где перекрывается больше всего теней FU (первый снизу при равенстве)
bool MaxOverlap(int d, ENUM_TIMEFRAMES tf, datetime since, double rl, double rh, double &ol, double &oh)
  {
   MqlRates m[];
   int sh = iBarShift(g_sym, tf, since);
   int n = Load(tf, MathMin(MathMax(sh + 2, 5), 700), m);
   double A[], B[];
   int k = 0;
   for(int j = 1; j < n - 1; j++)
     {
      if(m[j].time < since) break;
      if(FUClass(m, j, d) < 1) continue;
      double a = MathMax(Lv(m, j, d), rl), b = MathMin(BBot(m, j, d), rh);
      if(b - a < 0.01) continue;
      ArrayResize(A, k + 1);
      ArrayResize(B, k + 1);
      A[k] = a; B[k] = b; k++;
     }
   if(k == 0) return false;
   double P[];
   ArrayResize(P, 2 * k);
   for(int i = 0; i < k; i++) { P[2 * i] = A[i]; P[2 * i + 1] = B[i]; }
   ArraySort(P);
   int best = 0;
   double bl = 0, bh = 0;
   for(int i = 0; i < 2 * k - 1; i++)
     {
      double x1 = P[i], x2 = P[i + 1];
      if(x2 - x1 < 1e-9) continue;
      int cov = 0;
      for(int q = 0; q < k; q++)
         if(A[q] <= x1 + 1e-9 && B[q] >= x2 - 1e-9) cov++;
      if(cov > best) { best = cov; bl = x1; bh = x2; }
      else if(cov == best && cov > 0 && MathAbs(x1 - bh) < 1e-9) bh = x2;
     }
   if(best == 0) return false;
   ol = bl; oh = bh;
   return true;
  }

// Сужение до 3..10 внутри самого сильного источника группы [§6.2, True Stop]
void NarrowGroup(int d, double alo, double ahi, datetime aT0, double &lo, double &hi)
  {
   if(hi - lo > InpZoneMax)
     {
      double rl = MathMax(lo, alo), rh = MathMin(hi, ahi);
      if(rh - rl < 0.01) { rl = lo; rh = hi; }
      ENUM_TIMEFRAMES tfs[2] = {PERIOD_M15, PERIOD_M5};
      for(int t = 0; t < 2 && rh - rl > InpZoneMax; t++)
        {
         double ol, oh;
         if(MaxOverlap(d, tfs[t], aT0, rl, rh, ol, oh)) { rl = ol; rh = oh; }
        }
      lo = rl; hi = rh;
     }
   if(hi - lo > InpZoneMax) hi = lo + InpZoneMax;
   if(hi - lo < InpZoneMin) hi = lo + InpZoneMin;
  }

//+------------------------------------------------------------------+
//| Источники зон [§6.1]                                             |
//+------------------------------------------------------------------+
void AddCand(SCand &c[], int src, ENUM_TIMEFRAMES tf, double lo, double hi, datetime t0,
             int pts, bool fuAfter, double lvl, string label)
  {
   int k = ArraySize(c);
   ArrayResize(c, k + 1);
   c[k].src = src; c[k].tf = tf; c[k].lo = lo; c[k].hi = hi; c[k].t0 = t0;
   c[k].pts = pts; c[k].fuAfter = fuAfter; c[k].lvl = lvl; c[k].label = label;
  }

// Грубый отбор кандидатов (3 радиуса); точный радиус проверяется по готовой зоне
bool NearWide(int d, double lo, double hi)
  {
   double pm = d * SymbolInfoDouble(g_sym, SYMBOL_BID);
   return lo <= pm && (pm - hi) <= 3.0 * InpZoneRadius;
  }

// Источник 1: Order Block (odd-свеча против движения, зона = тело)
void GenOB(ENUM_TIMEFRAMES tf, int d, SCand &c[])
  {
   MqlRates r[];
   int n = Load(tf, tf == PERIOD_H4 ? 70 : (tf == PERIOD_H1 ? 130 : 200), r);
   int cOdd = 0, cWick = 0, cMove = 0, cDead = 0, cFar = 0, cNoFU = 0, cOk = 0, cChk = 0;
   int pts = tf == PERIOD_H4 ? 3 : (tf == PERIOD_H1 ? 2 : 1);
   for(int i = 2; i < n - 1; i++)
     {
      datetime t0 = r[i].time + PeriodSeconds(tf);
      if(TimeCurrent() - t0 > LifeSec(tf)) break;
      cChk++;
      double rg = Rng(r, i, d);
      if(rg <= 0 || !(Cv(r, i, d) < Ov(r, i, d))) { cOdd++; continue; }
      double top = BTop(r, i, d), bot = BBot(r, i, d);
      string tag = StringFormat("OB %s %s %s", TFName(tf), DirName(d), TimeToString(r[i].time, TIME_DATE | TIME_MINUTES));
      if(rg - (top - bot) > InpOBWickPct / 100.0 * rg)
        { cWick++; if(LogOnce(tag + "w")) Lg(2, "Отброшен " + tag + ": тени"); continue; }
      bool moved = false;
      for(int k = i - 1; k >= MathMax(1, i - InpOBMoveBars); k--)
         if(Cv(r, k, d) > Hv(r, i, d)) { moved = true; break; }
      if(!moved)
        { cMove++; if(LogOnce(tag + "m")) Lg(2, "Отброшен " + tag + ": moved"); continue; }
      bool dead = false;
      for(int k = i - 1; k >= 1; k--)
         if(Cv(r, k, d) < bot) { dead = true; break; }
      if(dead)
        { cDead++; if(LogOnce(tag + "d")) Lg(2, "Отброшен " + tag + ": dead"); continue; }
      bool fu = false;
      for(int j = i - 1; j >= MathMax(1, i - 5); j--)
         if(IsFU(r, j, d) && Lv(r, j, d) <= top) { fu = true; break; }
      if(InpOBNeedFU && !fu)
        { cNoFU++; if(LogOnce(tag + "f")) Lg(2, "Отброшен " + tag + ": нет FU"); continue; }
      if(!NearWide(d, bot, top))
        { cFar++; if(LogOnce(tag + "r")) Lg(2, "Отброшен " + tag + ": дальше радиуса"); continue; }
      AddCand(c, 1, tf, bot, top, t0, pts, fu, 0, TFName(tf) + " OB");
      cOk++;
     }
   Lg(2, StringFormat("OB %s %s: проверено %d, odd-отказ %d, тени %d, moved %d, dead %d, нет FU %d, радиус %d, принято %d",
                      TFName(tf), DirName(d), cChk, cOdd, cWick, cMove, cDead, cNoFU, cFar, cOk));
  }

// Источник 2: тень FU на H1 и M15
void GenFU(ENUM_TIMEFRAMES tf, int d, SCand &c[])
  {
   MqlRates r[];
   int n = Load(tf, tf == PERIOD_H1 ? 80 : 200, r);
   int pts = tf == PERIOD_H1 ? 2 : 1;
   for(int i = 1; i < n - 1; i++)
     {
      datetime t0 = r[i].time + PeriodSeconds(tf);
      if(TimeCurrent() - t0 > LifeSec(tf)) break;
      if(FUClass(r, i, d) < 1) continue;
      double a = Lv(r, i, d), b = BBot(r, i, d);
      if(b - a < InpFUZoneMinWick) continue;
      if(!NearWide(d, a, b)) continue;
      AddCand(c, 2, tf, a, b, t0, pts, false, 0, TFName(tf) + " FU");
     }
  }

void AddLiq(int d, double price)
  {
   for(int k = 0; k < ArraySize(g_liq); k++)
      if(g_liqDir[k] == d && MathAbs(g_liq[k] - price) < 0.05) return;
   int n = ArraySize(g_liq);
   ArrayResize(g_liq, n + 1);
   ArrayResize(g_liqDir, n + 1);
   g_liq[n] = price;
   g_liqDir[n] = d;
  }

// Источник 3: ещё не снятая ликвидность (равные уровни, swing, Doji) -> зона за уровнем
void GenLiq(ENUM_TIMEFRAMES tf, int d, SCand &c[])
  {
   MqlRates r[];
   int n = Load(tf, tf == PERIOD_H1 ? 80 : 200, r);
   for(int i = 1; i < n - 1; i++)
     {
      datetime t0 = r[i].time + PeriodSeconds(tf);
      if(TimeCurrent() - t0 > LifeSec(tf)) break;
      bool sw = IsSwingLow(r, i, d);
      bool dj = !sw && IsDoji(r, i) && !IsFU(r, i, 1) && !IsFU(r, i, -1);
      if(!sw && !dj) continue;
      double L = Lv(r, i, d);
      bool swept = false;
      for(int k = 1; k < i; k++)
         if(Lv(r, k, d) < L) { swept = true; break; }
      if(swept) continue;
      AddLiq(d, d * L);
      int pts = 2;
      string nm = d > 0 ? (sw ? "SWL" : "DOJI") : (sw ? "SWH" : "DOJI");
      if(sw)
        {
         datetime lim = (datetime)((long)TimeCurrent() - (long)InpZoneLifeHours * 3600);
         for(int j = i + 1; j < n - 2; j++)
           {
            if(r[j].time < lim) break;
            if(IsSwingLow(r, j, d) && MathAbs(Lv(r, j, d) - L) <= InpEqTol)
              {
               pts = 3;
               nm = d > 0 ? "EQL" : "EQH";
               break;
              }
           }
        }
      double lo = L - InpSweepZone, hi = L + 1.0;
      if(!NearWide(d, lo, hi)) continue;
      AddCand(c, 3, tf, lo, hi, t0, pts, false, L, StringFormat("%s %s %s", TFName(tf), nm, Px(d * L)));
     }
  }

//+------------------------------------------------------------------+
//| Слияние, баллы, учёт зон [§6.2]                                  |
//+------------------------------------------------------------------+
int CandStrength(const SCand &c)
  {
   if(c.src == 1 || c.src == 3) return 10 + c.pts;   // OB H4/H1, EQH/EQL сильнее теней FU
   return c.pts;
  }

void UpsertGroup(int d, SCand &c[], int &mem[], int ai, double glo, double ghi)
  {
   double nlo = glo, nhi = ghi;
   NarrowGroup(d, c[ai].lo, c[ai].hi, c[ai].t0, nlo, nhi);

   // в баллы и подпись идут только источники, попавшие в суженную зону
   int obP = 0, fuP = 0, nFU = 0, liqP = 0;
   bool fuAfter = false;
   datetime formed = 0;
   ENUM_TIMEFRAMES topTF = PERIOD_M15;
   int topRank = 0;
   string sOb = "", sFu = "", sLq = "";
   double lv[6];
   int nl = 0;
   for(int q = 0; q < ArraySize(mem); q++)
     {
      int k = mem[q];
      if(k != ai && (c[k].hi < nlo || c[k].lo > nhi)) continue;
      if(c[k].src == 1)
        {
         obP = MathMax(obP, c[k].pts);
         if(c[k].fuAfter) fuAfter = true;
         if(StringFind(sOb, c[k].label) < 0) sOb += (sOb == "" ? "" : " + ") + c[k].label;
        }
      else if(c[k].src == 2)
        {
         fuP = MathMax(fuP, c[k].pts);
         nFU++;
         if(StringFind(sFu, c[k].label) < 0) sFu += (sFu == "" ? "" : " + ") + c[k].label;
        }
      else
        {
         liqP = MathMax(liqP, c[k].pts);
         if(StringFind(sLq, c[k].label) < 0) sLq += (sLq == "" ? "" : " + ") + c[k].label;
         if(nl < 6 && c[k].lvl >= nlo - 2.0 && c[k].lvl <= nhi + 2.0) { lv[nl] = d * c[k].lvl; nl++; }
        }
      formed = MathMax(formed, c[k].t0);          // зона образовалась с последним попавшим в неё источником
      int rk = c[k].tf == PERIOD_H4 ? 3 : (c[k].tf == PERIOD_H1 ? 2 : 1);
      if(rk > topRank) { topRank = rk; topTF = c[k].tf; }
     }
   string srcs = sOb;
   if(sFu != "") srcs += (srcs == "" ? "" : " + ") + sFu;
   if(sLq != "") srcs += (srcs == "" ? "" : " + ") + sLq;
   int srcScore = obP + fuP + MathMin(3, MathMax(0, nFU - 1)) + liqP + (fuAfter ? 1 : 0);

   double rulo = d > 0 ? glo : -ghi, ruhi = d > 0 ? ghi : -glo;
   double zlo = NormalizeDouble(d > 0 ? nlo : -nhi, g_digits);
   double zhi = NormalizeDouble(d > 0 ? nhi : -nlo, g_digits);

   // поиск существующей зоны по объединению источников; одна зона обновляется один раз за пересчёт
   int act = -1;
   bool deadCover = false;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dir != d || g_zones[i].updId == g_rebuildId) continue;
      if(g_zones[i].uhi < rulo - 1.0 || g_zones[i].ulo > ruhi + 1.0) continue;
      if(g_zones[i].status <= ZONE_CONFIRMED) { act = i; break; }
      if(g_zones[i].newest >= formed) deadCover = true;
     }
   if(act < 0 && deadCover)
     {
      string tag = StringFormat("%s %s–%s", DirName(d), Px(zlo), Px(zhi));
      if(LogOnce("dup" + tag + TimeToString(formed))) Lg(2, "Кандидат " + tag + " | " + srcs + ": уже построена");
      return;
     }
   if(act < 0 && !NearPrice(d, d > 0 ? zlo : -zhi, d > 0 ? zhi : -zlo))
     {
      string tag = StringFormat("%s %s–%s", DirName(d), Px(zlo), Px(zhi));
      if(LogOnce("far" + tag + TimeToString(formed))) Lg(2, "Кандидат " + tag + " | " + srcs + ": дальше радиуса");
      return;
     }

   bool isNew = (act < 0);
   int idx = act;
   if(isNew)
     {
      idx = ArraySize(g_zones);
      ArrayResize(g_zones, idx + 1);
      g_zones[idx].dir = d;
      g_zones[idx].hasHCS = false;
      g_zones[idx].notified = false;
      g_zones[idx].lastSkip = "";
      g_zones[idx].lastSkipT = 0;
      g_zones[idx].status = ZONE_NEW;
      g_zones[idx].tradable = false;
      g_zones[idx].lo = zlo;
      g_zones[idx].hi = zhi;
      g_zones[idx].born = formed;
      g_zones[idx].newest = formed;
     }
   bool rangeChanged = isNew || (g_zones[idx].status == ZONE_NEW &&
                                 (MathAbs(g_zones[idx].lo - zlo) > 0.005 || MathAbs(g_zones[idx].hi - zhi) > 0.005));
   if(!isNew && rangeChanged)
     {
      g_zones[idx].lo = zlo;
      g_zones[idx].hi = zhi;
      g_zones[idx].born = formed;
      g_zones[idx].newest = formed;
     }
   g_zones[idx].updId = g_rebuildId;
   g_zones[idx].ulo = rulo;
   g_zones[idx].uhi = ruhi;
   g_zones[idx].newest = MathMax(g_zones[idx].newest, formed);
   g_zones[idx].topTF = topTF;
   g_zones[idx].srcs = srcs;
   g_zones[idx].srcScore = srcScore;
   g_zones[idx].nLvl = nl;
   for(int k = 0; k < nl; k++) g_zones[idx].lvl[k] = lv[k];
   g_zones[idx].htf = HtfMatch(d, g_zones[idx].lo, g_zones[idx].hi, g_zones[idx].lvl4);
   int bonus = (g_zones[idx].htf ? 1 : 0) + (ZoneInFVG(g_zones[idx].lo, g_zones[idx].hi) ? -2 : 0) +
               (g_zones[idx].hasHCS ? 2 : 0);
   g_zones[idx].score = srcScore + bonus;
   if(rangeChanged) ReplayZone(g_zones[idx]);
   if(isNew)
     {
      if(g_zones[idx].status == ZONE_INVALID)
         Lg(2, ZoneLine(g_zones[idx]) + " | создана и сразу INVALID");
      else
         Lg(1, ZoneLine(g_zones[idx]) + " | новая → " + StatusName(g_zones[idx].status));
     }
   ZoneNotify(g_zones[idx]);
  }

void SelectTradable()
  {
   for(int i = 0; i < ArraySize(g_zones); i++) g_zones[i].tradable = false;
   for(int side = -1; side <= 1; side += 2)
      for(int pick = 0; pick < InpMaxZonesSide; pick++)
        {
         int bi = -1;
         double bd = DBL_MAX;
         for(int i = 0; i < ArraySize(g_zones); i++)
           {
            if(g_zones[i].dir != side || g_zones[i].tradable) continue;
            if(g_zones[i].status > ZONE_CONFIRMED || !ZoneInRadius(g_zones[i])) continue;
            double dist = ZoneDist(g_zones[i]);
            if(dist < bd) { bd = dist; bi = i; }
           }
         if(bi >= 0) g_zones[bi].tradable = true;
        }
  }

void PruneZones()
  {
   for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
      if(TimeCurrent() - g_zones[i].newest > 5 * 86400)
        {
         int last = ArraySize(g_zones) - 1;
         if(i != last) g_zones[i] = g_zones[last];
         ArrayResize(g_zones, last);
        }
  }

// Пересчёт зон на каждой новой свече M15 [§6.3]
void RebuildZones()
  {
   g_rebuildId++;
   ArrayResize(g_liq, 0);
   ArrayResize(g_liqDir, 0);
   for(int s = 0; s < 2; s++)
     {
      int d = s == 0 ? 1 : -1;
      if((d > 0 && !InpTradeBuy) || (d < 0 && !InpTradeSell)) continue;
      FindHTFLevels(d);
      SCand c[];
      ArrayResize(c, 0);
      GenOB(PERIOD_H4, d, c);
      GenOB(PERIOD_H1, d, c);
      if(InpUseM15OB) GenOB(PERIOD_M15, d, c);
      GenFU(PERIOD_H1, d, c);
      GenFU(PERIOD_M15, d, c);
      GenLiq(PERIOD_H1, d, c);
      GenLiq(PERIOD_M15, d, c);
      int n = ArraySize(c);
      bool used[];
      ArrayResize(used, n);
      for(int k = 0; k < n; k++) used[k] = false;
      while(true)
        {
         // якорь кластера: самый сильный кандидат, при равенстве самый свежий
         int ai = -1, bst = -1;
         for(int k = 0; k < n; k++)
           {
            if(used[k]) continue;
            int st = CandStrength(c[k]);
            if(st > bst || (st == bst && ai >= 0 && c[k].t0 > c[ai].t0)) { bst = st; ai = k; }
           }
         if(ai < 0) break;
         double glo = c[ai].lo, ghi = c[ai].hi;
         double limit = MathMax(2.0 * InpZoneMax, ghi - glo);   // группа не шире 2×InpZoneMax
         int mem[];
         ArrayResize(mem, 1);
         mem[0] = ai;
         used[ai] = true;
         bool added = true;
         while(added)
           {
            added = false;
            for(int k = 0; k < n; k++)
              {
               if(used[k]) continue;
               if(c[k].lo > ghi + 1.0 || c[k].hi < glo - 1.0) continue;
               double nl2 = MathMin(glo, c[k].lo), nh2 = MathMax(ghi, c[k].hi);
               if(nh2 - nl2 > limit) continue;
               glo = nl2; ghi = nh2;
               used[k] = true;
               int m = ArraySize(mem);
               ArrayResize(mem, m + 1);
               mem[m] = k;
               added = true;
              }
           }
         UpsertGroup(d, c, mem, ai, glo, ghi);
        }
     }
   PruneZones();
   SelectTradable();
   int ns = 0, nb = 0;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].status > ZONE_CONFIRMED || !ZoneInRadius(g_zones[i])) continue;
      if(g_zones[i].dir > 0) nb++; else ns++;
     }
   Lg(1, StringFormat("Bias D1=%s H4=%s | зон SELL: %d, BUY: %d", BiasName(g_d1), BiasName(g_h4), ns, nb));
  }

// Статусы на каждой новой свече M5
void UpdateZoneStates()
  {
   MqlRates m5[], m15[];
   bool h5 = Load(PERIOD_M5, 5, m5) >= 3;
   bool h15 = Load(PERIOD_M15, 5, m15) >= 3;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].status > ZONE_CONFIRMED) continue;
      if(TimeCurrent() - g_zones[i].newest > LifeSec(g_zones[i].topTF))
        {
         SetStatus(g_zones[i], ZONE_INVALID, "возраст");
         continue;
        }
      if(h15) CheckInvalidM15(g_zones[i], m15, 1);
      if(g_zones[i].status > ZONE_CONFIRMED) continue;
      if(h5) AdvanceNew(g_zones[i], m5, 1);
      ZoneNotify(g_zones[i]);
     }
   SelectTradable();
  }

//+------------------------------------------------------------------+
//| Триггеры A-D [§7]                                                |
//+------------------------------------------------------------------+
int TrigPts(string trig, bool strong)
  {
   if(trig == "A") return 1;
   if(trig == "B") return 2;
   if(trig == "C") return strong ? 2 : 0;
   return 0;
  }

void FillSig(SSig &s, string trig, int cls, bool strong, double ext, string mark, datetime t)
  {
   s.ok = true; s.trig = trig; s.cls = cls; s.strong = strong; s.ext = ext; s.mark = mark; s.t = t;
   s.pts = TrigPts(trig, strong) + (cls == 3 ? 2 : (cls == 2 ? 1 : 0));
  }

void AddWhy(string &why, string m)
  {
   if(StringFind(why, m) >= 0) return;
   why += (why == "" ? "" : "; ") + m;
  }

// A: Manipulation Cycle (точки 1 и 2 ждём, вход на 3)
bool TriggerA(const SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   int d = z.dir, n = ArraySize(r);
   double zl = ZLo(z), zh = ZHi(z);
   int k = -1;
   for(int x = 3; x <= 30 && x + 1 < n; x++)
     {
      double L = Lv(r, x, d);
      if(L < Lv(r, x - 1, d) && L < Lv(r, x + 1, d) && L >= zl - InpPierceMax && L <= zh) { k = x; break; }
     }
   if(k < 0) { AddWhy(why, "A: нет точки 1 в зоне"); return false; }
   double prevLow = Lv(r, k, d);
   double pierce = DBL_MAX;
   int jp = -1;
   for(int q = 1; q < k; q++)
      if(Lv(r, q, d) < pierce) { pierce = Lv(r, q, d); jp = q; }
   if(jp < 0 || !(pierce < prevLow)) { AddWhy(why, "A: точка 1 " + Px(d * prevLow) + " не проколота"); return false; }
   if(pierce < zl - InpPierceMax)
     {
      AddWhy(why, StringFormat("A: прокол %s дальше допуска %s", Px(d * pierce), Px(d * (zl - InpPierceMax))));
      return false;
     }
   if(jp > InpTrigLookback) { AddWhy(why, "A: прокол старше " + IntegerToString(InpTrigLookback) + " свечей"); return false; }
   if(!(Cv(r, 1, d) > prevLow)) { AddWhy(why, "A: нет закрытия обратно за " + Px(d * prevLow)); return false; }
   int cls = MathMax(FUClass(r, 1, d), FUClass(r, jp, d));
   if(cls < 1 && (IsLowerWick(r, jp, d) || IsLowerWick(r, 1, d))) cls = 1;
   if(cls < (int)InpMinFUClass)
     {
      AddWhy(why, StringFormat("A: FU класс %s < %s", ClsName(cls), ClsName((int)InpMinFUClass)));
      return false;
     }
   FillSig(s, "A", cls, false, pierce, "FU", r[1].time);
   return true;
  }

// B: HCS — FU-2 вернулся в тень FU-1 в зоне
bool TriggerB(const SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   int d = z.dir, n = ArraySize(r);
   double zl = ZLo(z), zh = ZHi(z);
   int cls = FUClass(r, 1, d);
   if(cls < 1) return false;
   double L2 = Lv(r, 1, d), ext = L2;
   int cnt = 0;
   for(int m = 3; m <= 60 && m + 1 < n; m++)
     {
      if(!IsFU(r, m, d)) continue;
      double L1 = Lv(r, m, d), b1 = BBot(r, m, d);
      if(L1 < zl - InpPierceMax || L1 > zh) continue;
      if(L2 >= L1 - InpHCSTol && L2 <= b1) { cnt++; ext = MathMin(ext, L1); }
     }
   if(cnt == 0) return false;
   FillSig(s, "B", cls, false, ext, "HCS", r[1].time);
   return true;
  }

// C: Negation в зоне
bool TriggerC(const SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   int d = z.dir;
   int ng = Negation(r, 1, d);
   if(ng == 0) return false;
   double zl = ZLo(z), zh = ZHi(z);
   double deep = MathMin(Lv(r, 1, d), Lv(r, 2, d));
   if(deep < zl - InpPierceMax || deep > zh) return false;
   FillSig(s, "C", FUClass(r, 1, d), ng == 2, deep, "NEG", r[1].time);
   return true;
  }

// D: снятие ликвидности (равные уровни, swing, Doji) у зоны и закрытие обратно за уровнем
bool TriggerD(const SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   int d = z.dir, n = ArraySize(r);
   double zl = ZLo(z), zh = ZHi(z);
   double lv[];
   int nl = 0;
   for(int k = 0; k < z.nLvl; k++) { ArrayResize(lv, nl + 1); lv[nl] = d * z.lvl[k]; nl++; }
   for(int k = 0; k < ArraySize(g_liq); k++)
      if(g_liqDir[k] == d) { ArrayResize(lv, nl + 1); lv[nl] = d * g_liq[k]; nl++; }
   int lb = MathMax(1, MathMin(InpTrigLookback, n - 8));
   bool anyNear = false;
   for(int k = 0; k < nl; k++)
     {
      double m = lv[k];
      if(m < zl - InpPierceMax || m > zh + InpPierceMax) continue;
      anyNear = true;
      bool old = false;                       // уровень снят ещё до окна поиска
      for(int j = lb + 1; j <= lb + 6 && j < n; j++)
         if(Lv(r, j, d) < m - InpLqMin) { old = true; break; }
      if(old) continue;
      int js = -1;
      for(int j = lb; j >= 1; j--)
         if(Lv(r, j, d) < m - InpLqMin) { js = j; break; }
      if(js < 0) continue;
      if(!(Cv(r, 1, d) > m)) { AddWhy(why, "D: после снятия " + Px(d * m) + " нет закрытия обратно"); continue; }
      double ext = DBL_MAX;
      for(int j = 1; j <= js; j++) ext = MathMin(ext, Lv(r, j, d));
      int cls = MathMax(FUClass(r, 1, d), FUClass(r, js, d));
      bool wick = IsLowerWick(r, js, d) || IsLowerWick(r, 1, d);
      if(cls < 1 && !wick) { AddWhy(why, "D: снятие " + Px(d * m) + " без тени и FU"); continue; }
      if(cls < 1) cls = 1;
      FillSig(s, "D", cls, false, ext, "SWEEP", r[1].time);
      return true;
     }
   if(!anyNear) AddWhy(why, "D: нет уровней ликвидности у зоны");
   return false;
  }

// E: отбой от зоны — свеча коснулась зоны и закрылась в сторону сделки с тенью к зоне
bool TriggerE(const SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   int d = z.dir;
   double zl = ZLo(z), zh = ZHi(z);
   double L = Lv(r, 1, d);
   if(L > zh) return false;                                   // зону не задела
   if(L < zl - InpPierceMax) { AddWhy(why, "E: свеча ушла за зону дальше допуска"); return false; }
   if(!(Cv(r, 1, d) > Ov(r, 1, d))) { AddWhy(why, "E: свеча не развернулась"); return false; }
   double rg = Rng(r, 1, d);
   if(rg <= 0) return false;
   double wick = BBot(r, 1, d) - L;
   if(wick < InpTouchWickPct / 100.0 * rg) { AddWhy(why, "E: тень к зоне меньше " + DoubleToString(InpTouchWickPct, 0) + "%"); return false; }
   if(!(Cv(r, 1, d) >= (zl + zh) / 2.0)) { AddWhy(why, "E: закрытие глубоко в зоне"); return false; }
   FillSig(s, "E", FUClass(r, 1, d), false, L, "TOUCH", r[1].time);
   return true;
  }

// Прямой вход: BIW, за которым сразу Doji
bool TriggerDirect(const SZone &z, const MqlRates &r[], SSig &s)
  {
   int d = z.dir;
   if(!IsDoji(r, 1) || !IsBIW(r, 2, d)) return false;
   double deep = MathMin(Lv(r, 1, d), Lv(r, 2, d));
   if(deep < ZLo(z) - InpPierceMax || deep > ZHi(z)) return false;
   FillSig(s, "DIRECT", 0, false, deep, "BIW", r[1].time);
   return true;
  }

bool EvalZone(SZone &z, const MqlRates &r[], SSig &s, string &why)
  {
   s.ok = false; s.pts = 0; s.cls = 0; s.trig = ""; s.ext = 0; s.mark = ""; s.t = 0; s.strong = false;
   SSig best, t;
   best.ok = false; best.pts = -1;
   string w = "";
   t.ok = false;
   if(InpUseTrigB && TriggerB(z, r, t, w))
     {
      z.hasHCS = true;
      z.score = z.srcScore + (z.htf ? 1 : 0) + 2 + (ZoneInFVG(z.lo, z.hi) ? -2 : 0);
      if(t.pts > best.pts) best = t;
     }
   t.ok = false;
   if(InpUseTrigA && TriggerA(z, r, t, w) && t.pts > best.pts) best = t;
   t.ok = false;
   if(InpUseTrigD && TriggerD(z, r, t, w) && t.pts > best.pts) best = t;
   t.ok = false;
   if(InpUseTrigC && TriggerC(z, r, t, w) && t.pts > best.pts) best = t;
   t.ok = false;
   t.ok = false;
   if(InpUseTrigE && !best.ok && TriggerE(z, r, t, w)) best = t;
   t.ok = false;
   if(InpDirectEntry && !best.ok && TriggerDirect(z, r, t)) best = t;
   if(!best.ok)
     {
      why = w == "" ? "нет триггера (A, B, C, D)" : w;
      return false;
     }
   s = best;
   return true;
  }

//+------------------------------------------------------------------+
//| Позиции, лот, маржа [§2, §8]                                     |
//+------------------------------------------------------------------+
bool FindPos(ulong &tk, long &id, int &d, double &vol, double &op, double &sl, double &tp)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      tk = t;
      id = PositionGetInteger(POSITION_IDENTIFIER);
      d = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      vol = PositionGetDouble(POSITION_VOLUME);
      op = PositionGetDouble(POSITION_PRICE_OPEN);
      sl = PositionGetDouble(POSITION_SL);
      tp = PositionGetDouble(POSITION_TP);
      return true;
     }
   return false;
  }

bool HasPos()
  {
   ulong tk; long id; int d; double v, o, s, t;
   return FindPos(tk, id, d, v, o, s, t);
  }

ENUM_RISK_MODE EffectiveRiskMode()
  {
   ENUM_RISK_MODE m = InpRiskMode;
   if((m == RISK_HALF || m == RISK_FULL) &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
      m = RISK_PERCENT;
   return m;
  }

double CalcLot(double slDist, int d, double entry)
  {
   double step = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_STEP);
   double minv = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
   double maxv = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   ENUM_RISK_MODE mode = EffectiveRiskMode();
   double lot;
   if(mode == RISK_FIXED)
      lot = InpFixedLot;
   else
     {
      double pct = mode == RISK_PERCENT ? InpRiskPercent : (mode == RISK_HALF ? 50.0 : 100.0);
      double risk = AccountInfoDouble(ACCOUNT_BALANCE) * pct / 100.0;
      double tv = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_VALUE);
      double ts = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_SIZE);
      if(tv <= 0 || ts <= 0) return 0;
      double lossPerLot = slDist / ts * tv;
      if(lossPerLot <= 0) return 0;
      lot = risk / lossPerLot;
     }
   lot = MathFloor(lot / step + 1e-9) * step;
   lot = MathMax(lot, minv);
   lot = MathMin(lot, maxv);
   ENUM_ORDER_TYPE ot = d > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   while(lot >= minv - 1e-12)
     {
      double m = 0;
      if(OrderCalcMargin(ot, g_sym, lot, entry, m) && m * (1.0 + InpMarginReserve / 100.0) <= free)
         return NormalizeDouble(lot, 2);
      lot -= step;
     }
   return 0;
  }

double MinStopDist()
  {
   double lvl = (double)MathMax(SymbolInfoInteger(g_sym, SYMBOL_TRADE_STOPS_LEVEL),
                                SymbolInfoInteger(g_sym, SYMBOL_TRADE_FREEZE_LEVEL));
   return lvl * g_point + g_point;
  }

//+------------------------------------------------------------------+
//| Статистика дня по истории счёта [§9]                             |
//+------------------------------------------------------------------+
SDay CalcDay()
  {
   SDay s;
   s.trades = 0; s.counter = 0; s.pts = 0; s.lossStreak = 0; s.lastLossBuy = 0; s.lastLossSell = 0;
   if(!HistorySelect(DayStartSrv(), TimeCurrent() + 86400)) return s;
   long ids[];
   double inP[], inV[], outW[], outV[], pnl[];
   datetime outT[];
   int dirs[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0) continue;
      if(HistoryDealGetInteger(tk, DEAL_MAGIC) != InpMagic) continue;
      if(HistoryDealGetString(tk, DEAL_SYMBOL) != g_sym) continue;
      long dt = HistoryDealGetInteger(tk, DEAL_TYPE);
      if(dt != DEAL_TYPE_BUY && dt != DEAL_TYPE_SELL) continue;
      long pid = HistoryDealGetInteger(tk, DEAL_POSITION_ID);
      int idx = -1;
      for(int q = 0; q < ArraySize(ids); q++)
         if(ids[q] == pid) { idx = q; break; }
      if(idx < 0)
        {
         idx = ArraySize(ids);
         ArrayResize(ids, idx + 1);  ArrayResize(inP, idx + 1);  ArrayResize(inV, idx + 1);
         ArrayResize(outW, idx + 1); ArrayResize(outV, idx + 1); ArrayResize(pnl, idx + 1);
         ArrayResize(outT, idx + 1); ArrayResize(dirs, idx + 1);
         ids[idx] = pid; inP[idx] = 0; inV[idx] = 0; outW[idx] = 0; outV[idx] = 0; pnl[idx] = 0;
         outT[idx] = 0; dirs[idx] = 0;
        }
      double vol = HistoryDealGetDouble(tk, DEAL_VOLUME);
      double px = HistoryDealGetDouble(tk, DEAL_PRICE);
      pnl[idx] += HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_SWAP) +
                  HistoryDealGetDouble(tk, DEAL_COMMISSION);
      long en = HistoryDealGetInteger(tk, DEAL_ENTRY);
      if(en == DEAL_ENTRY_IN)
        {
         inP[idx] = px; inV[idx] = vol;
         dirs[idx] = dt == DEAL_TYPE_BUY ? 1 : -1;
         s.trades++;
         if(StringFind(HistoryDealGetString(tk, DEAL_COMMENT), "COUNTER") >= 0) s.counter++;
        }
      else if(en == DEAL_ENTRY_OUT || en == DEAL_ENTRY_OUT_BY)
        {
         outW[idx] += px * vol; outV[idx] += vol;
         datetime tm = (datetime)HistoryDealGetInteger(tk, DEAL_TIME);
         if(tm > outT[idx]) outT[idx] = tm;
        }
     }
   int cl[];
   for(int q = 0; q < ArraySize(ids); q++)
      if(inV[q] > 0 && outV[q] >= inV[q] - 1e-9)
        {
         int k = ArraySize(cl);
         ArrayResize(cl, k + 1);
         cl[k] = q;
         s.pts += dirs[q] * (outW[q] / outV[q] - inP[q]);
        }
   for(int a = 0; a < ArraySize(cl); a++)
      for(int b = a + 1; b < ArraySize(cl); b++)
         if(outT[cl[b]] < outT[cl[a]]) { int tmp = cl[a]; cl[a] = cl[b]; cl[b] = tmp; }
   for(int a = 0; a < ArraySize(cl); a++)
     {
      int q = cl[a];
      if(pnl[q] < 0)
        {
         s.lossStreak++;
         if(dirs[q] > 0) s.lastLossBuy = MathMax(s.lastLossBuy, outT[q]);
         else s.lastLossSell = MathMax(s.lastLossSell, outT[q]);
        }
      else
         s.lossStreak = 0;
     }
   // запасной счётчик COUNTER, если брокер обрезал комментарий
   int key = (int)(DayStartSrv() / 86400);
   if(key != g_cntDayKey) { g_cntDayKey = key; g_cntToday = 0; }
   s.counter = MathMax(s.counter, g_cntToday);
   return s;
  }

//+------------------------------------------------------------------+
//| Отрисовка [§10]                                                  |
//+------------------------------------------------------------------+
void DrawZones()
  {
   if(!InpDraw) return;
   ObjectsDeleteAll(0, OBJ_PREFIX + "Z");
   int drawn = 0;
   datetime tEnd = TimeCurrent() + 4 * 3600;
   for(int i = 0; i < ArraySize(g_zones) && drawn < 2 * InpMaxZonesSide; i++)
     {
      SZone z = g_zones[i];
      if(z.status > ZONE_CONFIRMED || !z.tradable) continue;
      string nm = OBJ_PREFIX + "Z" + IntegerToString(drawn);
      color col = z.dir > 0 ? clrGreen : clrRed;
      ObjectCreate(0, nm, OBJ_RECTANGLE, 0, z.born, z.hi, tEnd, z.lo);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
      ObjectSetInteger(0, nm, OBJPROP_FILL, z.status == ZONE_CONFIRMED);
      ObjectSetInteger(0, nm, OBJPROP_STYLE, z.status == ZONE_CONFIRMED ? STYLE_SOLID : STYLE_DOT);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
      string tn = OBJ_PREFIX + "ZT" + IntegerToString(drawn);
      ObjectCreate(0, tn, OBJ_TEXT, 0, tEnd, z.hi);
      ObjectSetString(0, tn, OBJPROP_TEXT, StringFormat("%s · %s–%s · балл %d · %s", DirName(z.dir),
                      Px(z.lo), Px(z.hi), z.score, z.status == ZONE_CONFIRMED ? "CONF" : "NEW"));
      ObjectSetInteger(0, tn, OBJPROP_COLOR, col);
      ObjectSetInteger(0, tn, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, tn, OBJPROP_ANCHOR, ANCHOR_RIGHT_LOWER);
      drawn++;
     }
   ChartRedraw();
  }

void DrawMark(datetime t, double price, string text, color col)
  {
   if(!InpDraw) return;
   string nm = OBJ_PREFIX + "M" + IntegerToString(g_markIdx);
   g_markIdx = (g_markIdx + 1) % 4;
   ObjectDelete(0, nm);
   ObjectCreate(0, nm, OBJ_TEXT, 0, t, price);
   ObjectSetString(0, nm, OBJPROP_TEXT, text);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, 9);
  }

void DrawLine(string name, double price, color col)
  {
   if(!InpDraw) return;
   string nm = OBJ_PREFIX + name;
   if(ObjectFind(0, nm) < 0) ObjectCreate(0, nm, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, nm, OBJPROP_PRICE, price);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, STYLE_DASH);
  }

void ClearLines()
  {
   ObjectDelete(0, OBJ_PREFIX + "SL");
   ObjectDelete(0, OBJ_PREFIX + "TP");
  }

string NearestZoneText(int d)
  {
   int bi = -1;
   double bd = DBL_MAX;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dir != d || g_zones[i].status > ZONE_CONFIRMED || !ZoneInRadius(g_zones[i])) continue;
      double dist = ZoneDist(g_zones[i]);
      if(dist < bd) { bd = dist; bi = i; }
     }
   if(bi < 0) return "нет";
   return StringFormat("%s–%s %s (балл %d)", Px(g_zones[bi].lo), Px(g_zones[bi].hi),
                       g_zones[bi].status == ZONE_CONFIRMED ? "CONF" : "NEW", g_zones[bi].score);
  }

void DrawPanel()
  {
   if(!InpDraw) return;
   double sp = SymbolInfoDouble(g_sym, SYMBOL_ASK) - SymbolInfoDouble(g_sym, SYMBOL_BID);
   Comment("Mr. Casino v2.2\n",
           "Bias: D1=", BiasName(g_d1), "  H4=", BiasName(g_h4), "\n",
           "Сессия: ", InSession() ? "открыта" : "закрыта", " (Бишкек ", LocalHM(), ")\n",
           "SELL-зона: ", NearestZoneText(-1), "\n",
           "BUY-зона: ", NearestZoneText(1), "\n",
           "Риск: ", EnumToString(EffectiveRiskMode()), "\n",
           "Сделок сегодня: ", g_day.trades, " / ", InpMaxTradesDay, "  (COUNTER ", g_day.counter, ")\n",
           "Пунктов за день: ", DoubleToString(g_day.pts, 1), " / ", DoubleToString(InpDailyTarget, 0), "\n",
           "Спред: ", DoubleToString(sp, 2), "\n",
           "Пропуск: ", g_lastSkip);
  }

//+------------------------------------------------------------------+
//| Фильтры и вход [§7, §8, §9]                                      |
//+------------------------------------------------------------------+
string GlobalBlock()
  {
   if(HasPos()) return "позиция уже открыта";
   if(FridayCloseTime()) return "пятница, закрытие рынка";
   if(!InSession()) return "вне сессии (Бишкек " + LocalHM() + ")";
   if(g_day.trades >= InpMaxTradesDay) return StringFormat("лимит сделок дня %d", InpMaxTradesDay);
   if(g_day.pts >= InpDailyTarget) return StringFormat("дневная цель %.1f pt", g_day.pts);
   if(g_day.lossStreak >= InpMaxLossStreak) return StringFormat("%d стопа подряд, пауза до завтра", g_day.lossStreak);
   if(NewsBlock()) return "окно новостей";
   double spread = SymbolInfoDouble(g_sym, SYMBOL_ASK) - SymbolInfoDouble(g_sym, SYMBOL_BID);
   if(spread > InpMaxSpread) return StringFormat("спред %.2f > %.2f", spread, InpMaxSpread);
   return "";
  }

void Skip(int zi, string why)
  {
   g_lastSkip = why;
   if(g_zones[zi].lastSkip == why && TimeCurrent() - g_zones[zi].lastSkipT < 1800) return;
   g_zones[zi].lastSkip = why;
   g_zones[zi].lastSkipT = TimeCurrent();
   Lg(1, "Пропуск " + ZoneTag(g_zones[zi]) + ": " + why);
  }

bool InRegion(const SZone &z, const MqlRates &r[])
  {
   int d = z.dir;
   double zl = ZLo(z), zh = ZHi(z);
   for(int i = 1; i <= 2; i++)
      if(Hv(r, i, d) >= zl - InpPierceMax && Lv(r, i, d) <= zh + InpPierceMax) return true;
   return false;
  }

void TryEntry()
  {
   MqlRates r[];
   if(Load(PERIOD_M5, 90, r) < 50) return;
   string gblock = GlobalBlock();
   int bi = -1, bpts = -1, btype = T_NEUTRAL;
   SSig bs;
   bs.ok = false;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].status != ZONE_CONFIRMED || !g_zones[i].tradable) continue;
      int d = g_zones[i].dir;
      if(!InRegion(g_zones[i], r)) continue;
      if(gblock != "") { Skip(i, gblock); continue; }
      if((d > 0 && !InpTradeBuy) || (d < 0 && !InpTradeSell)) { Skip(i, "сторона выключена"); continue; }
      int tp = TradeType(d);
      if(tp == T_COUNTER)
        {
         if(!InpAllowCounter) { Skip(i, "COUNTER выключен"); continue; }
         if(g_day.counter >= InpMaxCounterDay) { Skip(i, StringFormat("лимит COUNTER %d в день", InpMaxCounterDay)); continue; }
        }
      datetime ll = d > 0 ? g_day.lastLossBuy : g_day.lastLossSell;
      if(ll > 0 && TimeCurrent() - ll < (long)InpRepeatM15Bars * PeriodSeconds(PERIOD_M15))
        { Skip(i, StringFormat("повтор %s после стопа раньше %d свечей M15", DirName(d), InpRepeatM15Bars)); continue; }
      SSig s;
      string why = "";
      if(!EvalZone(g_zones[i], r, s, why)) { Skip(i, why); continue; }
      int need = MinScore(tp);
      if(g_zones[i].score < need)
        { Skip(i, StringFormat("балл %d < %d (%s, триггер %s)", g_zones[i].score, need, TypeName(tp), s.trig)); continue; }
      if(tp == T_COUNTER && !(s.trig == "B" || (s.trig == "C" && s.strong) || s.cls == 3))
        { Skip(i, "COUNTER: нужен HCS, Strong Negation или FU класса A (триггер " + s.trig + ")"); continue; }
      int pts = g_zones[i].score + s.pts;
      if(pts > bpts) { bpts = pts; bi = i; bs = s; btype = tp; }
     }
   if(bi < 0) return;

   int d = g_zones[bi].dir;
   SZone z = g_zones[bi];
   if(InsideFVG(r)) { Skip(bi, "сигнальная свеча внутри незакрытого FVG (триггер " + bs.trig + ")"); return; }

   double ask = SymbolInfoDouble(g_sym, SYMBOL_ASK), bid = SymbolInfoDouble(g_sym, SYMBOL_BID);
   double spread = ask - bid;
   double entry = d > 0 ? ask : bid;
   double buf = spread + InpSLBuffer;
   double sl = d * bs.ext - d * buf;
   double dist = d * (entry - sl);
   double minDist = MathMax(MinStopDist(), InpMinSL);
   if(dist < minDist) { dist = minDist; sl = entry - d * dist; }
   if(dist > InpMaxSL)
     { Skip(bi, StringFormat("стоп %.1f > %.1f (триггер %s)", dist, InpMaxSL, bs.trig)); return; }

   double major = FindMajorLiq(d, entry);
   string obSrc;
   double obst = NearestObstacle(d, entry, major, obSrc);
   double tpDist = InpTP;
   if(obst > 0 && obst - 1.0 < tpDist) tpDist = obst - 1.0;
   if(tpDist < MinStopDist() || tpDist / dist < InpMinRR)
     {
      Skip(bi, StringFormat("RR %.1f < %.1f (тейк %.1f, стоп %.1f%s)", tpDist / dist, InpMinRR, tpDist, dist,
                            obSrc != "" ? ", препятствие: " + obSrc : ""));
      return;
     }

   sl = NormalizeDouble(sl, g_digits);
   double tp = NormalizeDouble(entry + d * tpDist, g_digits);
   double lot = CalcLot(dist, d, entry);
   if(lot <= 0) { Skip(bi, "не хватает маржи на минимальный лот"); return; }

   string tname = TypeName(btype);
   string cmt = "MC " + bs.trig + " " + tname;
   bool ok = d > 0 ? g_trade.Buy(lot, g_sym, 0.0, sl, tp, cmt) : g_trade.Sell(lot, g_sym, 0.0, sl, tp, cmt);
   uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      Skip(bi, StringFormat("ордер отклонён: %u %s", rc, g_trade.ResultRetcodeDescription()));
      return;
     }

   ulong tk; long id; int pd; double pv, po, psl, ptp;
   if(!FindPos(tk, id, pd, pv, po, psl, ptp)) { po = entry; id = 0; psl = sl; ptp = tp; pv = lot; }
   int total = bpts;
   g_pId = id; g_pDir = d; g_pTrig = bs.trig; g_pCls = bs.cls;
   g_pType = tname; g_pScore = z.score; g_pSrcs = z.srcs; g_pSess = SessionName();
   g_pStrength = Stars(total);
   g_pEntry = po; g_pSL = psl; g_pTP = ptp; g_pLot = pv; g_pLal = major;
   g_pZoneW = z.hi - z.lo; g_pZLo = z.lo; g_pZHi = z.hi;
   g_pBEAt = MathMin(InpBEAt, 0.5 * MathAbs(ptp - po));
   g_beDone = false; g_partDone = false; g_exitTag = ""; g_closeRetry = 0;
   SetStatus(g_zones[bi], ZONE_USED, "сделка");
   if(btype == T_COUNTER) g_cntToday++;
   g_day = CalcDay();

   Lg(0, StringFormat("СДЕЛКА %s | триггер %s | тип %s | балл зоны %d | вход %s SL %s TP %s | лот %.2f | сессия %s | %s",
                      ZoneTag(z), bs.trig, tname, z.score, Px(po), Px(psl), Px(ptp), pv, g_pSess, z.srcs));

   DrawMark(bs.t, d > 0 ? r[1].low : r[1].high, bs.mark, d > 0 ? clrLime : clrRed);
   DrawLine("SL", psl, clrRed);
   DrawLine("TP", ptp, clrLime);
   DrawZones();
   DrawPanel();

   Notify(StringFormat("%s %s · %s\nEntry: %s\nSL: %s\nTP: %s\nRR: 1:%.1f\n\n✅ Зона %s–%s · балл %d\n✅ %s\n✅ Триггер %s (%s), FU класс %s\n%sТФ: M5 · сессия %s\nSignal Strength: %s",
                       g_sym, DirName(d), tname,
                       Px(po), Px(psl), Px(ptp),
                       MathAbs(ptp - po) / MathMax(MathAbs(po - psl), 0.01),
                       Px(z.lo), Px(z.hi), z.score, z.srcs, bs.trig, bs.mark, ClsName(bs.cls),
                       z.htf ? "✅ HTF Re-test Level\n" : "", g_pSess, Stars(total)));
  }

//+------------------------------------------------------------------+
//| Сопровождение и закрытие [§8]                                    |
//+------------------------------------------------------------------+
void ClosePosNow(ulong tk, string tag)
  {
   g_exitTag = tag;
   if(!g_trade.PositionClose(tk))
      Print("Закрытие не удалось: ", g_trade.ResultRetcode(), " ", g_trade.ResultRetcodeDescription());
  }

void OnPositionClosed()
  {
   if(!HistorySelectByPosition(g_pId) || HistoryDealsTotal() == 0)
     {
      if(++g_closeRetry > 300) { g_pId = 0; ClearLines(); }
      return;
     }
   double pnl = 0, inP = 0, outW = 0, outV = 0, inV = 0;
   long reason = -1;
   datetime tOut = 0;
   int dir = g_pDir;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0) continue;
      long dt = HistoryDealGetInteger(tk, DEAL_TYPE);
      if(dt != DEAL_TYPE_BUY && dt != DEAL_TYPE_SELL) continue;
      pnl += HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_SWAP) +
             HistoryDealGetDouble(tk, DEAL_COMMISSION);
      long en = HistoryDealGetInteger(tk, DEAL_ENTRY);
      double vol = HistoryDealGetDouble(tk, DEAL_VOLUME), px = HistoryDealGetDouble(tk, DEAL_PRICE);
      if(en == DEAL_ENTRY_IN) { inP = px; inV = vol; dir = dt == DEAL_TYPE_BUY ? 1 : -1; }
      else if(en == DEAL_ENTRY_OUT || en == DEAL_ENTRY_OUT_BY)
        {
         outW += px * vol; outV += vol;
         reason = HistoryDealGetInteger(tk, DEAL_REASON);
         tOut = (datetime)HistoryDealGetInteger(tk, DEAL_TIME);
        }
     }
   if(outV <= 0 || (inV > 0 && outV < inV - 1e-9)) return;
   double outP = outW / outV;
   double pts = dir * (outP - inP);
   string ex = g_exitTag;
   if(ex == "")
     {
      if(reason == DEAL_REASON_TP) ex = "TP";
      else if(reason == DEAL_REASON_SL) ex = g_beDone && pts >= 0 ? "BE" : "SL";
      else ex = "OTHER";
     }
   string srcs = g_pSrcs;
   StringReplace(srcs, ",", " ");
   string line = StringFormat("%s,%s,%s,%s,%s,%d,%s,%.1f,%s,%s,%s,%s,%.2f,%.2f,%.2f,%s,%s",
                              TimeToString(tOut, TIME_DATE | TIME_MINUTES), DirName(dir), g_pType,
                              g_pTrig, ClsName(g_pCls), g_pScore, srcs, g_pZoneW, g_pSess,
                              Px(g_pEntry), Px(g_pSL), Px(g_pTP), g_pLot, pts, pnl, ex, g_pStrength);
   LogTrade(line);
   if(ex == "SL")                      // после стопа повторного входа в зону нет
      for(int zi = 0; zi < ArraySize(g_zones); zi++)
         if(g_zones[zi].dir == dir && MathAbs(g_zones[zi].lo - g_pZLo) < 0.01 && MathAbs(g_zones[zi].hi - g_pZHi) < 0.01)
            SetStatus(g_zones[zi], ZONE_INVALID, "стоп по зоне");
   Lg(0, StringFormat("ЗАКРЫТА %s %s | %s | %+.1f pt / %+.2f $ | вход %s выход %s", DirName(dir), g_pType, ex,
                      pts, pnl, Px(inP), Px(outP)));
   Notify(StringFormat("%s %s · %s закрыта (%s)\nВход: %s → Выход: %s\nРезультат: %+.1f pt / %+.2f $",
                       g_sym, DirName(dir), g_pType, ex, Px(inP), Px(outP), pts, pnl));
   g_pId = 0; g_exitTag = ""; g_closeRetry = 0;
   ClearLines();
   g_day = CalcDay();
   if(g_day.trades >= InpMaxTradesDay || g_day.pts >= InpDailyTarget || g_day.lossStreak >= InpMaxLossStreak)
      Notify(StringFormat("Дневной лимит достигнут: сделок %d, %+.1f pt, стопов подряд %d",
                          g_day.trades, g_day.pts, g_day.lossStreak));
   DrawZones();
   DrawPanel();
  }

void ManagePosition()
  {
   ulong tk; long id; int d; double vol, op, sl, tp;
   if(!FindPos(tk, id, d, vol, op, sl, tp))
     {
      if(g_pId != 0) OnPositionClosed();
      return;
     }
   if(g_pId == 0)                     // подхват позиции после перезапуска терминала
     {
      g_pId = id; g_pDir = d; g_pTrig = "RESTORED"; g_pCls = 0; g_pStrength = "-"; g_pType = "-";
      g_pScore = 0; g_pSrcs = "-"; g_pSess = "-";
      g_pEntry = op; g_pSL = sl; g_pTP = tp; g_pLot = vol;
      g_pLal = 0; g_beDone = d * (sl - op) >= 0; g_partDone = false; g_exitTag = ""; g_closeRetry = 0;
      g_pBEAt = MathMin(InpBEAt, tp > 0 ? 0.5 * MathAbs(tp - op) : InpBEAt);
     }
   if(FridayCloseTime()) { ClosePosNow(tk, "FRIDAY"); return; }

   double bid = SymbolInfoDouble(g_sym, SYMBOL_BID), ask = SymbolInfoDouble(g_sym, SYMBOL_ASK);
   double cur = d > 0 ? bid : ask;
   double profit = d * (cur - op);

   // безубыток: меньшее из InpBEAt и 50% пути до тейка
   if(!g_beDone && profit >= g_pBEAt)
     {
      double nsl = NormalizeDouble(op + d * InpBEOffset, g_digits);
      if(d * (nsl - sl) > 0 && d * (cur - nsl) >= MinStopDist())
        {
         if(g_trade.PositionModify(tk, nsl, tp))
           {
            g_beDone = true;
            g_pSL = nsl;
            DrawLine("SL", nsl, clrRed);
            Notify(StringFormat("%s %s: стоп в безубытке\nSL: %s", g_sym, DirName(d), Px(nsl)));
           }
        }
     }

   // частичное закрытие: 50% на первой LAL, только от 0.02 лота
   if(InpPartial && !g_partDone && g_pLal > 0 && vol >= 0.02 - 1e-9 && d * (cur - g_pLal) >= 0)
     {
      double step = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_STEP);
      double minv = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
      double cv = MathFloor(vol / 2.0 / step + 1e-9) * step;
      if(cv >= minv && vol - cv >= minv - 1e-12)
        {
         if(g_trade.PositionClosePartial(tk, NormalizeDouble(cv, 2))) g_partDone = true;
        }
      else
         g_partDone = true;
     }
  }

// Ранний выход: Negation против позиции на M15
void CheckEarlyExit()
  {
   ulong tk; long id; int d; double vol, op, sl, tp;
   if(!FindPos(tk, id, d, vol, op, sl, tp)) return;
   MqlRates m[];
   if(Load(PERIOD_M15, 10, m) < 6) return;
   if(Negation(m, 1, -d) > 0) ClosePosNow(tk, "EARLY");
  }

//+------------------------------------------------------------------+
//| Основной цикл                                                    |
//+------------------------------------------------------------------+
void OnNewM5()
  {
   g_day = CalcDay();

   datetime h1 = iTime(g_sym, PERIOD_H1, 0);
   if(h1 != g_lastH1)
     {
      g_lastH1 = h1;
      UpdateBias();
     }

   datetime m15 = iTime(g_sym, PERIOD_M15, 0);
   if(m15 != g_lastM15)
     {
      g_lastM15 = m15;
      RebuildZones();
      CheckEarlyExit();
     }

   UpdateZoneStates();
   DrawZones();
   DrawPanel();
   TryEntry();
  }

int OnInit()
  {
   g_sym = InpSymbol;
   if(_Symbol != g_sym)
     {
      Print("Робот торгует только ", g_sym, ". Откройте график ", g_sym, " (M5), сейчас: ", _Symbol);
      return INIT_FAILED;
     }
   if(!SymbolSelect(g_sym, true))
     {
      Print("Символ не найден в Обзоре рынка: ", g_sym);
      return INIT_FAILED;
     }
   g_digits = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);
   g_point = SymbolInfoDouble(g_sym, SYMBOL_POINT);
   g_isTester = (bool)MQLInfoInteger(MQL_TESTER);
   g_sessStart = ParseHM(InpSessionStart);
   g_sessEnd = ParseHM(InpSessionEnd);
   g_londonMin = ParseHM(InpLondonStart);
   g_nyMin = ParseHM(InpNYStart);
   ParseNews();
   if(Period() != PERIOD_M5)
      Print("Внимание: робот рассчитан на график M5.");

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)MathMax(1.0, MathRound(InpMaxSlip / g_point)));
   g_trade.SetTypeFillingBySymbol(g_sym);

   if(EffectiveRiskMode() != InpRiskMode)
      Print("Реальный счёт: режим риска ", EnumToString(InpRiskMode), " заменён на PERCENT.");

   ArrayResize(g_zones, 0);
   ArrayResize(g_lk, 0);
   g_hasLv[0] = false; g_hasLv[1] = false;
   g_lastM5 = 0; g_lastM15 = 0; g_lastH1 = 0;
   g_day = CalcDay();
   ManagePosition();
   Print("Mr. Casino v2.2 запущен: ", g_sym, ", риск ", EnumToString(EffectiveRiskMode()), ", магик ", InpMagic,
         ", лог-уровень ", InpLogLevel, ", Бишкек ", LocalHM());
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, OBJ_PREFIX);
   Comment("");
  }

void OnTick()
  {
   ManagePosition();
   datetime t = iTime(g_sym, PERIOD_M5, 0);
   if(t == 0) return;
   if(t != g_lastM5)
     {
      g_lastM5 = t;
      OnNewM5();
     }
  }
//+------------------------------------------------------------------+
