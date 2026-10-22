//+------------------------------------------------------------------+
//|                                                    MrCasino.mq5 |
//|   Советник MT5 по стратегии Mr. Casino (Flashcards), XAUUSD      |
//|   Реализация ТЗ "робот MT5 Mr. Casino (демо $100)"                |
//|                                                                  |
//|   Единицы: 1 пункт = $1 цены золота (все пороги в ценовых        |
//|   единицах, перевод в терминальные points не нужен).             |
//|   Разделы ТЗ помечены в комментариях как [§N].                   |
//+------------------------------------------------------------------+
#property copyright "Consigliere"
#property version   "1.00"
#property description "Mr. Casino Flashcards: OB -> FU/HCS/Negation -> вход из зоны 5-10 пунктов"

#include <Trade\Trade.mqh>

//--- перечисления
enum ENUM_RISK_MODE { RISK_FIXED = 0, RISK_PERCENT = 1, RISK_HALF = 2, RISK_FULL = 3 };
enum ENUM_TP_MODE   { TP_FIXED = 0, TP_LAL = 1 };
enum ENUM_MIN_FU    { FU_CLASS_A = 3, FU_CLASS_B = 2, FU_CLASS_C = 1 };

//--- входные параметры [§9]
input group "Символ и метка"
input string          InpSymbol        = "XAUUSD";     // Символ золота у XM (GOLD, GOLD#, XAUUSD#)
input long            InpMagic         = 777001;       // Magic Number

input group "Риск и лот [§2]"
input ENUM_RISK_MODE  InpRiskMode      = RISK_FIXED;   // Режим риска
input double          InpFixedLot      = 0.01;         // Лот для FIXED
input double          InpRiskPercent   = 10.0;         // % баланса для PERCENT
input double          InpMarginReserve = 30.0;         // Запас свободной маржи, %

input group "Зона [§5]"
input double          InpZoneMax       = 10.0;         // Макс. ширина зоны, пунктов
input double          InpZoneMin       = 3.0;          // Мин. ширина зоны, пунктов
input int             InpZoneLifeHours = 48;           // Срок жизни зоны, часов
input double          InpConfirmAway   = 10.0;         // Отход от зоны для CONFIRMED, пунктов
input double          InpInvalidBeyond = 2.0;          // Закрытие M15 за зоной для INVALID, пунктов

input group "Паттерны [§4]"
input double          InpDojiBodyPct   = 25.0;         // Тело Doji, % диапазона
input double          InpWickPct       = 40.0;         // Тень снизу/сверху у FU, %
input double          InpOBWickPct     = 30.0;         // Макс. сумма теней свечи OB, %
input double          InpLqMin         = 0.3;          // Прокол экстремума, пунктов
input ENUM_MIN_FU     InpMinFUClass    = FU_CLASS_B;   // Мин. класс FU для входа
input double          InpHCSTol        = 1.0;          // Допуск HCS (тень FU-2 в тени FU-1), пунктов
input double          InpEqTol         = 1.0;          // Допуск ровных хаёв/лоёв, пунктов

input group "Триггеры [§6]"
input bool            InpUseTrigA      = true;         // A: Manipulation Cycle
input bool            InpUseTrigB      = true;         // B: HCS
input bool            InpUseTrigC      = true;         // C: Negation
input bool            InpDirectEntry   = false;        // Прямой вход: BIW + Doji

input group "Стоп и тейк [§7]"
input double          InpMaxSL         = 12.0;         // Макс. стоп, пунктов
input double          InpSLBuffer      = 0.5;          // Буфер стопа сверх спреда, пунктов
input ENUM_TP_MODE    InpTPMode        = TP_FIXED;     // Режим тейка
input double          InpTP            = 40.0;         // Тейк для FIXED, пунктов
input double          InpMinRR         = 2.0;          // Мин. соотношение тейк/стоп
input double          InpBEAt          = 15.0;         // Безубыток при +N пунктов
input double          InpBEOffset      = 0.5;          // Стоп в безубытке = вход + N
input bool            InpPartial       = false;        // 50% на первой LAL
input int             InpFridayMin     = 30;           // Закрыть позиции за N минут до закрытия в пятницу

input group "Фильтры и лимиты [§8]"
input int             InpMaxTradesDay  = 3;            // Сделок в день
input double          InpDailyTarget   = 40.0;         // Дневная цель, пунктов
input int             InpMaxLossStreak = 2;            // Стопов подряд до паузы
input double          InpMaxSpread     = 0.5;          // Макс. спред, пунктов
input double          InpMaxSlip       = 0.3;          // Проскальзывание (deviation), пунктов
input string          InpSessionStart  = "13:00";      // Начало сессии (время Бишкека)
input string          InpSessionEnd    = "23:00";      // Конец сессии (время Бишкека)
input int             InpUtcOffset     = 6;            // Часовой пояс Бишкека относительно UTC
input string          InpNewsTimes     = "";           // Новости "ГГГГ.ММ.ДД ЧЧ:ММ;..." (время Бишкека)
input int             InpNewsMinutes   = 30;           // Окно вокруг новости, минут

input group "Уведомления и графика [§10]"
input string          InpTgToken       = "";           // Telegram bot token
input string          InpTgChatId      = "";           // Telegram chat id
input bool            InpDraw          = true;         // Рисовать зоны на графике
input string          InpLogFile       = "MrCasino_trades.csv"; // CSV-лог сделок (MQL5/Files)

//--- константы
#define ZONE_NEW        0
#define ZONE_CONFIRMED  1
#define ZONE_USED       2
#define ZONE_INVALID    3
#define OBJ_PREFIX      "MC_"

//--- структуры
struct SZone
  {
   int               dir;          // +1 BUY, -1 SELL
   ENUM_TIMEFRAMES   obTF;
   datetime          obTime;
   double            lo, hi;       // реальные цены
   datetime          srcTime;      // начало самой свежей FU зоны
   datetime          srcEnd;       // конец той же свечи
   datetime          confirmTime;
   int               status;
   bool              away;
   bool              htf;          // совпала с HTF Re-test Level
   double            lvl4;         // Level #4 (реальная цена)
   int               shadows;      // сколько теней FU сошлось
   bool              hasHCS;
   bool              tradable;     // входит в 2 ближайших на сторону
   bool              notified;
   string            best;
  };

struct SSig
  {
   bool              ok;
   string            trig;
   int               cls;          // класс FU: 3=A 2=B 1=C 0=нет
   int               strength;     // 1 AFU, 2 FU, 3 re-test, 4 HCS, 5 HCS x2/x3
   double            ext;          // самая дальняя точка манипуляции (зеркальные координаты)
   string            mark;
   datetime          t;
  };

struct SDay
  {
   int               trades;
   double            pts;
   int               lossStreak;
  };

//--- глобальные переменные
CTrade     g_trade;
string     g_sym;
int        g_digits;
double     g_point;
SZone      g_zones[];
int        g_bias       = 0;
bool       g_downgrade  = false;
double     g_lv[4];
bool       g_hasLv      = false;
datetime   g_lastM5     = 0;
datetime   g_lastM15    = 0;
datetime   g_lastH1     = 0;
datetime   g_news[];
int        g_sessStart  = 0;
int        g_sessEnd    = 0;
SDay       g_day;
bool       g_isTester   = false;
int        g_markIdx    = 0;

// открытая позиция робота
long       g_pId        = 0;
int        g_pDir       = 0;
string     g_pTrig      = "";
int        g_pCls       = 0;
string     g_pStrength  = "";
string     g_pZoneTF    = "";
double     g_pZoneW     = 0;
double     g_pEntry     = 0;
double     g_pSL        = 0;
double     g_pTP        = 0;
double     g_pLot       = 0;
double     g_pLal       = 0;
bool       g_beDone     = false;
bool       g_partDone   = false;
string     g_exitTag    = "";
int        g_zKeyDir    = 0;
ENUM_TIMEFRAMES g_zKeyTF = PERIOD_H1;
datetime   g_zKeyTime   = 0;
int        g_closeRetry = 0;

//+------------------------------------------------------------------+
//| Зеркальные аксессоры: для SELL цены отражаются, логика бычья     |
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
   int c = CopyRates(g_sym, tf, 0, n, r);
   return c;
  }

string TFName(ENUM_TIMEFRAMES tf) { return StringSubstr(EnumToString(tf), 7); }
string ClsName(int c) { return c == 3 ? "A" : (c == 2 ? "B" : (c == 1 ? "C" : "-")); }

//+------------------------------------------------------------------+
//| Паттерны [§4]                                                    |
//+------------------------------------------------------------------+
bool IsDoji(const MqlRates &r[], int i)
  {
   if(i >= ArraySize(r)) return false;
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

bool IsReversed(const MqlRates &r[], int i, int d)
  {
   return (Cv(r, i, d) > Ov(r, i, d)) != (Cv(r, i + 1, d) > Ov(r, i + 1, d));
  }

bool IsBIW(const MqlRates &r[], int i, int d)
  {
   return BBot(r, i, d) >= Lv(r, i + 1, d) && BTop(r, i, d) <= BBot(r, i + 1, d);
  }

// Класс FU по таблице FU Types: 3=A (SFU), 2=B, 1=C, 0=не FU
int FUClass(const MqlRates &r[], int i, int d)
  {
   if(!IsFU(r, i, d)) return 0;
   bool bull = Cv(r, i, d) > Ov(r, i, d);
   if(IsBIW(r, i, d) && bull) return 3;          // SFU тип 1
   if(IsEngulf(r, i, d)) return 3;               // SFU тип 2
   if(IsBodyCover(r, i, d) && IsLowerWick(r, i, d)) return 2; // AFU тип 3
   return 1;                                     // AFU типы 4-7
  }

// 0 нет, 1 обычный Negation, 2 Strong Negation. Сигнальная свеча i, отрицаемая i+1
int Negation(const MqlRates &r[], int i, int d)
  {
   if(i < 0 || i + 2 >= ArraySize(r)) return 0;
   if(!(Cv(r, i + 1, d) < Ov(r, i + 1, d))) return 0;
   double rg1 = Rng(r, i + 1, d);
   if(rg1 <= 0) return 0;
   if(BBot(r, i + 1, d) - Lv(r, i + 1, d) > 0.15 * rg1) return 0;
   if(!(Cv(r, i + 1, d) < Lv(r, i + 2, d))) return 0;       // лой забран телом
   if(!(Cv(r, i, d) > Ov(r, i, d))) return 0;
   if(!(Lv(r, i, d) < Lv(r, i + 1, d))) return 0;
   if(Cv(r, i, d) > Hv(r, i + 1, d)) return 2;
   if(Cv(r, i, d) > BTop(r, i + 1, d)) return 1;
   return 0;
  }

//+------------------------------------------------------------------+
//| Время [§8]                                                       |
//+------------------------------------------------------------------+
int GmtOffset()
  {
   double off = (double)(TimeCurrent() - TimeGMT());
   return (int)(MathRound(off / 900.0) * 900.0);
  }
datetime ToLocal(datetime srv)   { return srv - GmtOffset() + InpUtcOffset * 3600; }
datetime FromLocal(datetime loc) { return loc + GmtOffset() - InpUtcOffset * 3600; }

datetime DayStartSrv()
  {
   datetime l = ToLocal(TimeCurrent());
   datetime mid = l - (l % 86400);
   return FromLocal(mid);
  }

int ParseHM(string s)
  {
   string p[];
   if(StringSplit(s, ':', p) < 2) return 0;
   return (int)StringToInteger(p[0]) * 60 + (int)StringToInteger(p[1]);
  }

bool InSession()
  {
   datetime l = ToLocal(TimeCurrent());
   int mins = (int)((l % 86400) / 60);
   if(g_sessStart <= g_sessEnd) return mins >= g_sessStart && mins < g_sessEnd;
   return mins >= g_sessStart || mins < g_sessEnd;
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

// За InpFridayMin минут до закрытия рынка в пятницу
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
//| Telegram и лог [§10]                                             |
//+------------------------------------------------------------------+
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
   Print("[TG] ", text);
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

void LogTrade(string line)
  {
   int h = FileOpen(InpLogFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
     {
      Print("Лог: не открыть файл ", InpLogFile, ", err ", GetLastError());
      return;
     }
   if(FileSize(h) == 0)
      FileWriteString(h, "date,side,trigger,fu_class,zone_tf,zone_width,entry,sl,tp,lot,result_pts,result_usd,exit,strength\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| Bias [§3]                                                        |
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
   int d1 = CalcBias(PERIOD_D1);
   int h4 = CalcBias(PERIOD_H4);
   g_downgrade = false;
   if(d1 != 0)
     {
      g_bias = d1;
      g_downgrade = (h4 != 0 && h4 != d1);
     }
   else
      g_bias = h4;
  }

//+------------------------------------------------------------------+
//| HTF Re-test Levels (LAL Levels) на H4 [§7 слайдов, §5 ТЗ]        |
//+------------------------------------------------------------------+
bool FindHTFLevels(int d)
  {
   g_hasLv = false;
   MqlRates r[];
   int n = Load(PERIOD_H4, 60, r);
   if(n < 20) return false;
   for(int k = 1; k <= 40 && k + 1 < n; k++)
     {
      if(!IsDoji(r, k + 1)) continue;
      double rgD = r[k + 1].high - r[k + 1].low;
      double rgB = r[k].high - r[k].low;
      if(rgB < 1.5 * rgD) continue;
      if(BBot(r, k, d) - Lv(r, k, d) < InpWickPct / 100.0 * rgB) continue;
      double m1 = Hv(r, k + 1, d);
      double m3 = Lv(r, k + 1, d);
      g_lv[0] = d * m1;
      g_lv[1] = d * (m1 + m3) / 2.0;
      g_lv[2] = d * m3;
      g_lv[3] = d * (Lv(r, k, d) + BBot(r, k, d)) / 2.0;
      g_hasLv = true;
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Зоны [§5]                                                        |
//+------------------------------------------------------------------+
int FindZone(int d, ENUM_TIMEFRAMES tf, datetime obT)
  {
   for(int i = 0; i < ArraySize(g_zones); i++)
      if(g_zones[i].dir == d && g_zones[i].obTF == tf && g_zones[i].obTime == obT) return i;
   return -1;
  }

void ZoneNotify(SZone &z)
  {
   if(z.status != ZONE_CONFIRMED || z.notified) return;
   z.notified = true;
   SendTG(StringFormat("%s %s: зона CONFIRMED\n%s OB · %s - %s (%.1f pt)\nЛучшая манипуляция: %s",
                       g_sym, z.dir > 0 ? "BUY" : "SELL", TFName(z.obTF),
                       DoubleToString(z.lo, g_digits), DoubleToString(z.hi, g_digits),
                       z.hi - z.lo, z.best));
  }

// Статус NEW -> CONFIRMED: цена ушла на 10+ пунктов и вернулась
void AdvanceNew(SZone &z, const MqlRates &r[], int j)
  {
   if(z.status != ZONE_NEW) return;
   int d = z.dir;
   if(z.away)
     {
      if(Lv(r, j, d) <= ZHi(z))
        {
         z.status = ZONE_CONFIRMED;
         z.confirmTime = r[j].time;
        }
     }
   else if(Hv(r, j, d) >= ZHi(z) + InpConfirmAway)
      z.away = true;
  }

void CheckInvalidM15(SZone &z, const MqlRates &m[], int j)
  {
   if(z.status > ZONE_CONFIRMED) return;
   int d = z.dir;
   double c = Cv(m, j, d);
   if(c < ZLo(z) - InpInvalidBeyond) { z.status = ZONE_INVALID; return; }
   if(z.htf && c < d * z.lvl4) z.status = ZONE_INVALID;
  }

// Прогон истории после построения зоны
void ReplayZone(SZone &z)
  {
   z.away = false;
   z.status = ZONE_NEW;
   MqlRates m5[], m15[];
   int sh5 = iBarShift(g_sym, PERIOD_M5, z.srcEnd);
   int n5 = Load(PERIOD_M5, MathMin(MathMax(sh5 + 2, 5), 900), m5);
   for(int j = n5 - 1; j >= 1; j--)
      if(m5[j].time >= z.srcEnd) AdvanceNew(z, m5, j);
   int sh15 = iBarShift(g_sym, PERIOD_M15, z.srcEnd);
   int n15 = Load(PERIOD_M15, MathMin(MathMax(sh15 + 2, 5), 400), m15);
   for(int j = n15 - 1; j >= 1; j--)
      if(m15[j].time >= z.srcEnd) CheckInvalidM15(z, m15, j);
  }

// Сужение тела OB до зоны 5-10 пунктов (зеркальные координаты). [§5 п.1-6]
bool NarrowZone(ENUM_TIMEFRAMES obTf, datetime obT, int d, double bot, double top,
                double &zlo, double &zhi, datetime &src, datetime &srcEnd, int &cnt)
  {
   MqlRates m[];
   datetime from = obT + PeriodSeconds(obTf);
   int sh = iBarShift(g_sym, PERIOD_M15, from);
   if(sh < 2) return false;
   int n = Load(PERIOD_M15, MathMin(sh + 2, 700), m);
   bool found = false;
   cnt = 0;
   for(int j = 1; j < n - 1; j++)
     {
      if(m[j].time < from) break;
      if(FUClass(m, j, d) < 1) continue;
      double a = Lv(m, j, d), b = BBot(m, j, d);
      if(a < bot - 2.0 || a > top) continue;
      if(!found)
        {
         zlo = a; zhi = b;
         src = m[j].time;
         srcEnd = src + PeriodSeconds(PERIOD_M15);
         found = true;
         cnt = 1;
        }
      else
        {
         double il = MathMax(a, zlo), ih = MathMin(b, zhi);
         if(il <= ih) { zlo = il; zhi = ih; cnt++; }
        }
     }
   if(!found) return false;
   if(TimeCurrent() - src > InpZoneLifeHours * 3600) return false;

   // уточнение на M5, если шире максимума
   if(zhi - zlo > InpZoneMax)
     {
      MqlRates f[];
      int sh5 = iBarShift(g_sym, PERIOD_M5, src);
      int n5 = Load(PERIOD_M5, MathMin(MathMax(sh5 + 2, 5), 500), f);
      bool f5 = false;
      double cl = 0, ch = 0;
      datetime s5 = 0;
      for(int j = 1; j < n5 - 1; j++)
        {
         if(f[j].time < src) break;
         if(FUClass(f, j, d) < 1) continue;
         double a = Lv(f, j, d), b = BBot(f, j, d);
         if(a < zlo - 1.0 || a > zhi) continue;
         if(!f5) { cl = a; ch = b; s5 = f[j].time; f5 = true; }
         else
           {
            double il = MathMax(a, cl), ih = MathMin(b, ch);
            if(il <= ih) { cl = il; ch = ih; cnt++; }
           }
        }
      if(f5)
        {
         double il = MathMax(cl, zlo), ih = MathMin(ch, zhi);
         if(il <= ih) { zlo = il; zhi = ih; }
         else { zlo = cl; zhi = ch; }
         if(s5 > src) { src = s5; srcEnd = s5 + PeriodSeconds(PERIOD_M5); }
        }
     }
   if(zhi - zlo > InpZoneMax) zhi = zlo + InpZoneMax;
   if(zhi - zlo < InpZoneMin) zhi = zlo + InpZoneMin;
   return true;
  }

void UpsertZone(int d, ENUM_TIMEFRAMES tf, datetime obT, double zlo, double zhi,
                datetime src, datetime srcEnd, int cnt)
  {
   double rlo = d > 0 ? zlo : -zhi;
   double rhi = d > 0 ? zhi : -zlo;
   int idx = FindZone(d, tf, obT);
   if(idx >= 0 && g_zones[idx].status != ZONE_NEW) return;
   if(idx < 0)
     {
      idx = ArraySize(g_zones);
      ArrayResize(g_zones, idx + 1);
      g_zones[idx].dir = d;
      g_zones[idx].obTF = tf;
      g_zones[idx].obTime = obT;
      g_zones[idx].hasHCS = false;
      g_zones[idx].notified = false;
      g_zones[idx].confirmTime = 0;
     }
   g_zones[idx].lo = NormalizeDouble(rlo, g_digits);
   g_zones[idx].hi = NormalizeDouble(rhi, g_digits);
   g_zones[idx].srcTime = src;
   g_zones[idx].srcEnd = srcEnd;
   g_zones[idx].shadows = cnt;
   g_zones[idx].tradable = false;
   g_zones[idx].htf = false;
   g_zones[idx].lvl4 = 0;
   if(g_hasLv)
     {
      for(int k = 0; k < 4; k++)
         if(g_lv[k] >= g_zones[idx].lo - 2.0 && g_lv[k] <= g_zones[idx].hi + 2.0)
            g_zones[idx].htf = true;
      g_zones[idx].lvl4 = g_lv[3];
     }
   g_zones[idx].best = g_zones[idx].hasHCS ? "HCS" : (cnt > 1 ? StringFormat("FU x%d", cnt) : "FU");
   ReplayZone(g_zones[idx]);
   ZoneNotify(g_zones[idx]);
  }

// Поиск OB на TF по направлению [§4 Order Block]
void BuildZonesForTF(ENUM_TIMEFRAMES tf, int d)
  {
   MqlRates r[];
   int n = Load(tf, tf == PERIOD_H4 ? 70 : 130, r);
   for(int i = 4; i < n - 1; i++)
     {
      double rg = Rng(r, i, d);
      if(rg <= 0) continue;
      if(!(Cv(r, i, d) < Ov(r, i, d))) continue;                       // «odd» против движения
      double top = BTop(r, i, d), bot = BBot(r, i, d);
      if(rg - (top - bot) > InpOBWickPct / 100.0 * rg) continue;        // почти без теней
      bool moved = false;
      for(int k = i - 1; k >= i - 3; k--)
         if(Cv(r, k, d) > Hv(r, i, d)) { moved = true; break; }
      if(!moved) continue;
      bool fu = false;
      for(int j = i - 1; j >= MathMax(1, i - 5); j--)
         if(IsFU(r, j, d) && Lv(r, j, d) <= top) { fu = true; break; }
      if(!fu) continue;
      bool dead = false;
      for(int k = i - 1; k >= 1; k--)
         if(Cv(r, k, d) < bot) { dead = true; break; }
      if(dead) continue;

      double zlo, zhi;
      datetime src, srcEnd;
      int cnt;
      if(!NarrowZone(tf, r[i].time, d, bot, top, zlo, zhi, src, srcEnd, cnt)) continue;
      UpsertZone(d, tf, r[i].time, zlo, zhi, src, srcEnd, cnt);
     }
  }

// Не более 2 зон на сторону — ближайшие к цене
void SelectTradable()
  {
   double price = SymbolInfoDouble(g_sym, SYMBOL_BID);
   for(int i = 0; i < ArraySize(g_zones); i++) g_zones[i].tradable = false;
   for(int side = -1; side <= 1; side += 2)
      for(int pick = 0; pick < 2; pick++)
        {
         int bi = -1;
         double bd = DBL_MAX;
         for(int i = 0; i < ArraySize(g_zones); i++)
           {
            if(g_zones[i].dir != side || g_zones[i].tradable) continue;
            if(g_zones[i].status > ZONE_CONFIRMED) continue;
            double dist = 0;
            if(price > g_zones[i].hi) dist = price - g_zones[i].hi;
            else if(price < g_zones[i].lo) dist = g_zones[i].lo - price;
            if(dist < bd) { bd = dist; bi = i; }
           }
         if(bi >= 0) g_zones[bi].tradable = true;
        }
  }

void PruneZones()
  {
   for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
     {
      if(TimeCurrent() - g_zones[i].srcTime > 5 * 86400)
        {
         int last = ArraySize(g_zones) - 1;
         if(i != last) g_zones[i] = g_zones[last];
         ArrayResize(g_zones, last);
        }
     }
  }

void RebuildZones()
  {
   UpdateBias();
   g_hasLv = false;
   if(g_bias != 0)
     {
      FindHTFLevels(g_bias);
      BuildZonesForTF(PERIOD_H4, g_bias);
      BuildZonesForTF(PERIOD_H1, g_bias);
     }
   PruneZones();
   SelectTradable();
  }

// Статусы зон на каждой новой свече M5
void UpdateZoneStates()
  {
   MqlRates m5[], m15[];
   bool h5 = Load(PERIOD_M5, 5, m5) >= 3;
   bool h15 = Load(PERIOD_M15, 5, m15) >= 3;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].status > ZONE_CONFIRMED) continue;
      if(TimeCurrent() - g_zones[i].srcTime > InpZoneLifeHours * 3600)
        {
         g_zones[i].status = ZONE_INVALID;
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
//| Ликвидность [§9 Calc. Lqd, §7 тейк]                              |
//+------------------------------------------------------------------+
// Ближайшая Major ликвидность в сторону сделки; 0 если нет
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
         // уровень не должен быть уже снят более новыми свечами
         bool swept = false;
         for(int k = 1; k < i; k++)
            if(Hv(r, k, d) > lvl) { swept = true; break; }
         if(swept) continue;
         // 1. Doji (не AFU)
         if(IsDoji(r, i) && !IsFU(r, i, 1) && !IsFU(r, i, -1))
            if(lvl > e && lvl < best) best = lvl;
         // 2. ровные хаи (swing high + пара в допуске)
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
         // 4. верх свечи FU старшего ТФ
         if(tfs[t] != PERIOD_M15 && IsFU(r, i, d))
            if(lvl > e && lvl < best) best = lvl;
        }
     }
   // 3. незакрытые FVG (M5 и старше), противоположный тип: нижняя граница gap
   ENUM_TIMEFRAMES ft[3] = {PERIOD_M5, PERIOD_M15, PERIOD_H1};
   for(int t = 0; t < 3; t++)
     {
      MqlRates r[];
      int n = Load(ft[t], 150, r);
      for(int k = 2; k < n - 1; k++)
        {
         // gap между Hv[k-1] (новее) и Lv[k+1] (старше): Hv[k-1] < Lv[k+1]
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

// Сигнальная свеча M5 внутри незакрытого FVG 3+ пункта [§6 отмена]
bool InsideFVG(const MqlRates &r[])
  {
   int n = ArraySize(r);
   double c = r[1].close;
   for(int k = 3; k < MathMin(n - 1, 45); k++)
     {
      // бычий FVG: Low[k-1] > High[k+1]
      double bot = r[k + 1].high, top = r[k - 1].low;
      if(top - bot >= 3.0)
        {
         bool filled = false;
         for(int q = k - 2; q >= 1; q--)
            if(r[q].low <= bot) { filled = true; break; }
         if(!filled && c >= bot && c <= top) return true;
        }
      // медвежий FVG: High[k-1] < Low[k+1]
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

//+------------------------------------------------------------------+
//| Триггеры [§6]                                                    |
//+------------------------------------------------------------------+
// A: Manipulation Cycle (ждать 1 и 2, вход на 3)
bool TriggerA(const SZone &z, const MqlRates &r[], SSig &s)
  {
   int d = z.dir, n = ArraySize(r);
   double zl = ZLo(z), zh = ZHi(z);
   int cls = FUClass(r, 1, d);
   if(cls < (int)InpMinFUClass) return false;
   int k = -1;
   for(int x = 3; x <= 30 && x + 1 < n; x++)
     {
      double L = Lv(r, x, d);
      if(L < Lv(r, x - 1, d) && L < Lv(r, x + 1, d) && L >= zl && L <= zh) { k = x; break; }
     }
   if(k < 0) return false;
   double prevLow = Lv(r, k, d);                       // точка 1
   double pierce = DBL_MAX;
   for(int q = 1; q < k; q++) pierce = MathMin(pierce, Lv(r, q, d));
   if(!(pierce < prevLow)) return false;               // точка 2
   if(pierce < zl - 2.0) return false;
   if(!(Cv(r, 1, d) > prevLow)) return false;          // точка 3
   s.ok = true; s.trig = "A"; s.cls = cls;
   s.strength = cls == 3 ? 2 : 1;
   s.ext = pierce; s.mark = "FU"; s.t = r[1].time;
   return true;
  }

// B: HCS — FU-2 перетестировал тень FU-1 внутри зоны
bool TriggerB(const SZone &z, const MqlRates &r[], SSig &s)
  {
   int d = z.dir, n = ArraySize(r);
   double zl = ZLo(z), zh = ZHi(z);
   int cls = FUClass(r, 1, d);
   if(cls < (int)InpMinFUClass) return false;
   double L2 = Lv(r, 1, d), ext = L2;
   int cnt = 0;
   for(int m = 3; m <= 60 && m + 1 < n; m++)
     {
      if(!IsFU(r, m, d)) continue;
      double L1 = Lv(r, m, d), b1 = BBot(r, m, d);
      if(L1 < zl - 2.0 || L1 > zh) continue;
      if(L2 >= L1 - InpHCSTol && L2 <= b1) { cnt++; ext = MathMin(ext, L1); }
     }
   if(cnt == 0) return false;
   s.ok = true; s.trig = "B"; s.cls = cls;
   s.strength = cnt >= 2 ? 5 : 4;
   s.ext = ext; s.mark = "HCS"; s.t = r[1].time;
   return true;
  }

// C: Negation в зоне
bool TriggerC(const SZone &z, const MqlRates &r[], SSig &s)
  {
   int d = z.dir;
   int ng = Negation(r, 1, d);
   if(ng == 0) return false;
   double zl = ZLo(z), zh = ZHi(z);
   double deep = MathMin(Lv(r, 1, d), Lv(r, 2, d));
   if(deep < zl - 2.0 || deep > zh) return false;
   s.ok = true; s.trig = "C"; s.cls = FUClass(r, 1, d);
   s.strength = ng == 2 ? 2 : 1;
   s.ext = deep; s.mark = "NEG"; s.t = r[1].time;
   return true;
  }

// Прямой вход: BIW, за которым сразу Doji, в зоне
bool TriggerDirect(const SZone &z, const MqlRates &r[], SSig &s)
  {
   int d = z.dir;
   if(!IsDoji(r, 1) || !IsBIW(r, 2, d)) return false;
   double zl = ZLo(z), zh = ZHi(z);
   double deep = MathMin(Lv(r, 1, d), Lv(r, 2, d));
   if(deep < zl - 2.0 || deep > zh) return false;
   s.ok = true; s.trig = "DIRECT"; s.cls = 0; s.strength = 1;
   s.ext = deep; s.mark = "BIW"; s.t = r[1].time;
   return true;
  }

bool EvalZone(SZone &z, const MqlRates &r[], SSig &s)
  {
   s.ok = false; s.strength = 0; s.cls = 0; s.trig = ""; s.ext = 0; s.mark = ""; s.t = 0;
   SSig a, b, c, dd, best;
   a.ok = b.ok = c.ok = dd.ok = false;
   best.ok = false; best.strength = 0;
   if(InpUseTrigB && TriggerB(z, r, b)) { z.hasHCS = true; z.best = "HCS"; if(b.strength > best.strength) best = b; }
   if(InpUseTrigA && TriggerA(z, r, a) && a.strength > best.strength) best = a;
   if(InpUseTrigC && TriggerC(z, r, c) && c.strength > best.strength) best = c;
   if(InpDirectEntry && !best.ok && TriggerDirect(z, r, dd)) best = dd;
   if(!best.ok) return false;
   s = best;
   return true;
  }

string StrengthName(int st)
  {
   switch(st)
     {
      case 1:  return "AFU";
      case 2:  return "FU";
      case 3:  return "FU re-test";
      case 4:  return "HCS";
      default: return "HCS x2/x3";
     }
  }

//+------------------------------------------------------------------+
//| Позиции, лот, маржа [§2, §7]                                     |
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
      m = RISK_PERCENT;               // на реале FULL/HALF блокируются
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
   // проверка маржи с запасом
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
//| Отрисовка [§10]                                                  |
//+------------------------------------------------------------------+
void DrawZones()
  {
   if(!InpDraw) return;
   ObjectsDeleteAll(0, OBJ_PREFIX + "Z");
   int drawn = 0;
   for(int i = 0; i < ArraySize(g_zones) && drawn < 4; i++)
     {
      SZone z = g_zones[i];
      if(z.status > ZONE_CONFIRMED || !z.tradable) continue;
      string nm = OBJ_PREFIX + "Z" + IntegerToString(drawn);
      color col = z.dir > 0 ? clrGreen : clrRed;
      ObjectCreate(0, nm, OBJ_RECTANGLE, 0, z.srcTime, z.hi, z.srcTime + InpZoneLifeHours * 3600, z.lo);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
      ObjectSetInteger(0, nm, OBJPROP_FILL, z.status == ZONE_CONFIRMED);
      ObjectSetInteger(0, nm, OBJPROP_STYLE, z.status == ZONE_CONFIRMED ? STYLE_SOLID : STYLE_DOT);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
      string tn = OBJ_PREFIX + "ZT" + IntegerToString(drawn);
      ObjectCreate(0, tn, OBJ_TEXT, 0, z.srcTime, z.hi);
      ObjectSetString(0, tn, OBJPROP_TEXT, StringFormat("%s OB · %.0f pt · %s", TFName(z.obTF), z.hi - z.lo, z.best));
      ObjectSetInteger(0, tn, OBJPROP_COLOR, col);
      ObjectSetInteger(0, tn, OBJPROP_FONTSIZE, 8);
      drawn++;
     }
   ChartRedraw();
  }

void DrawMark(datetime t, double price, string text, color col)
  {
   if(!InpDraw) return;
   string nm = OBJ_PREFIX + "M" + IntegerToString(g_markIdx);
   g_markIdx = (g_markIdx + 1) % 2;
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

void DrawPanel()
  {
   if(!InpDraw) return;
   double sp = SymbolInfoDouble(g_sym, SYMBOL_ASK) - SymbolInfoDouble(g_sym, SYMBOL_BID);
   string b = g_bias > 0 ? "BUY" : (g_bias < 0 ? "SELL" : "NONE");
   if(g_downgrade) b += " (H4 против D1)";
   Comment("Mr. Casino\n",
           "Bias: ", b, "\n",
           "Риск: ", EnumToString(EffectiveRiskMode()), "\n",
           "Сделок сегодня: ", g_day.trades, " / ", InpMaxTradesDay, "\n",
           "Пунктов за день: ", DoubleToString(g_day.pts, 1), " / ", DoubleToString(InpDailyTarget, 0), "\n",
           "Стопов подряд: ", g_day.lossStreak, "\n",
           "Спред: ", DoubleToString(sp, 2));
  }

//+------------------------------------------------------------------+
//| Статистика дня по истории счёта со своим Magic [§8]              |
//+------------------------------------------------------------------+
SDay CalcDay()
  {
   SDay s;
   s.trades = 0; s.pts = 0; s.lossStreak = 0;
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
        }
      else if(en == DEAL_ENTRY_OUT || en == DEAL_ENTRY_OUT_BY)
        {
         outW[idx] += px * vol; outV[idx] += vol;
         datetime tm = (datetime)HistoryDealGetInteger(tk, DEAL_TIME);
         if(tm > outT[idx]) outT[idx] = tm;
        }
     }
   // закрытые позиции по времени закрытия
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
      if(pnl[cl[a]] < 0) s.lossStreak++;
      else s.lossStreak = 0;
     }
   return s;
  }

//+------------------------------------------------------------------+
//| Фильтры входа [§8]                                               |
//+------------------------------------------------------------------+
bool FiltersOK()
  {
   if(g_bias == 0) return false;
   if(HasPos()) return false;
   if(FridayCloseTime()) return false;
   if(g_day.trades >= InpMaxTradesDay) return false;
   if(g_day.pts >= InpDailyTarget) return false;
   if(g_day.lossStreak >= InpMaxLossStreak) return false;
   if(!InSession()) return false;
   if(NewsBlock()) return false;
   double spread = SymbolInfoDouble(g_sym, SYMBOL_ASK) - SymbolInfoDouble(g_sym, SYMBOL_BID);
   if(spread > InpMaxSpread) { Print("Вход пропущен: спред ", DoubleToString(spread, 2)); return false; }
   return true;
  }

//+------------------------------------------------------------------+
//| Вход [§6, §7]                                                    |
//+------------------------------------------------------------------+
void TryEntry()
  {
   if(!FiltersOK()) return;
   MqlRates r[];
   if(Load(PERIOD_M5, 90, r) < 50) return;

   int bi = -1;
   SSig bs;
   bs.ok = false;
   int bscore = -1, bstr = 0;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dir != g_bias || g_zones[i].status != ZONE_CONFIRMED || !g_zones[i].tradable) continue;
      SSig s;
      if(!EvalZone(g_zones[i], r, s)) continue;
      int str = s.strength + (g_zones[i].htf ? 1 : 0) - (g_downgrade ? 1 : 0);
      str = MathMax(1, MathMin(6, str));
      int score = str * 10 + ((g_zones[i].htf || g_zones[i].hasHCS) ? 5 : 0);
      if(score > bscore) { bscore = score; bi = i; bs = s; bstr = str; }
     }
   if(bi < 0) return;

   int d = g_bias;
   SZone z = g_zones[bi];
   if(InsideFVG(r)) { Print("Вход отменён: сигнальная свеча внутри FVG"); return; }

   double ask = SymbolInfoDouble(g_sym, SYMBOL_ASK), bid = SymbolInfoDouble(g_sym, SYMBOL_BID);
   double spread = ask - bid;
   double entry = d > 0 ? ask : bid;
   double buf = spread + InpSLBuffer;
   double sl = d * bs.ext - d * buf;
   double dist = d * (entry - sl);
   double minDist = MinStopDist();
   if(dist < minDist) { dist = minDist; sl = entry - d * dist; }
   if(dist > InpMaxSL) { Print("Вход отменён: стоп ", DoubleToString(dist, 1), " > ", DoubleToString(InpMaxSL, 1)); return; }

   double major = FindMajorLiq(d, entry);
   double liqDist = major > 0 ? d * (major - entry) : 0;
   if(major > 0 && liqDist < InpMinRR * dist)
     { Print("Вход отменён: до ликвидности ", DoubleToString(liqDist, 1), " меньше ", DoubleToString(InpMinRR, 1), "R"); return; }
   double tpDist;
   if(InpTPMode == TP_LAL)
      tpDist = major > 0 ? liqDist : InpTP;
   else
      tpDist = (major > 0 && liqDist < InpTP) ? liqDist : InpTP;
   if(tpDist < minDist || tpDist / dist < InpMinRR) { Print("Вход отменён: RR ниже минимума"); return; }

   sl = NormalizeDouble(sl, g_digits);
   double tp = NormalizeDouble(entry + d * tpDist, g_digits);
   double lot = CalcLot(dist, d, entry);
   if(lot <= 0) { Print("Вход отменён: не хватает маржи"); return; }

   string cmt = "MC " + bs.trig;
   bool ok = d > 0 ? g_trade.Buy(lot, g_sym, 0.0, sl, tp, cmt) : g_trade.Sell(lot, g_sym, 0.0, sl, tp, cmt);
   uint rc = g_trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      Print("Ордер отклонён: ", rc, " ", g_trade.ResultRetcodeDescription());
      return;
     }

   ulong tk; long id; int pd; double pv, po, psl, ptp;
   if(!FindPos(tk, id, pd, pv, po, psl, ptp)) { po = entry; id = 0; psl = sl; ptp = tp; pv = lot; }
   g_pId = id; g_pDir = d; g_pTrig = bs.trig; g_pCls = bs.cls;
   g_pStrength = StrengthName(bstr) + (z.htf ? " + HTF Level" : "");
   g_pZoneTF = TFName(z.obTF); g_pZoneW = z.hi - z.lo;
   g_pEntry = po; g_pSL = psl; g_pTP = ptp; g_pLot = pv; g_pLal = major;
   g_beDone = false; g_partDone = false; g_exitTag = ""; g_closeRetry = 0;
   g_zKeyDir = z.dir; g_zKeyTF = z.obTF; g_zKeyTime = z.obTime;
   g_zones[bi].status = ZONE_USED;
   g_day = CalcDay();

   DrawMark(bs.t, d > 0 ? r[1].low : r[1].high, bs.mark, d > 0 ? clrLime : clrRed);
   DrawLine("SL", psl, clrRed);
   DrawLine("TP", ptp, clrLime);
   DrawZones();
   DrawPanel();

   string tpTxt = DoubleToString(ptp, g_digits);
   SendTG(StringFormat("%s %s\nEntry: %s\nSL: %s\nTP: %s\nRR: 1:%.1f\n\n✅ OB %s · зона %.1f pt\n✅ Триггер %s (%s)\n✅ FU класс %s\n%s%s\nТФ: M5\nSignal Strength: %s%s",
                       g_sym, d > 0 ? "BUY" : "SELL",
                       DoubleToString(po, g_digits), DoubleToString(psl, g_digits), tpTxt,
                       MathAbs(ptp - po) / MathMax(MathAbs(po - psl), 0.01),
                       g_pZoneTF, g_pZoneW, bs.trig, bs.mark, ClsName(bs.cls),
                       z.htf ? "✅ HTF Re-test Level\n" : "",
                       z.hasHCS ? "✅ HCS в зоне\n" : "",
                       StrengthName(bstr), z.htf ? " (+HTF)" : ""));
  }

//+------------------------------------------------------------------+
//| Сопровождение и закрытие [§7]                                    |
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
   if(outV <= 0 || (inV > 0 && outV < inV - 1e-9)) return;   // ещё закрыта не полностью
   double outP = outW / outV;
   double pts = dir * (outP - inP);
   string ex = g_exitTag;
   if(ex == "")
     {
      if(reason == DEAL_REASON_TP) ex = "TP";
      else if(reason == DEAL_REASON_SL) ex = g_beDone && pts >= 0 ? "BE" : "SL";
      else ex = "OTHER";
     }
   if(reason == DEAL_REASON_SL && g_pZoneTF != "")
     {
      int zi = FindZone(g_zKeyDir, g_zKeyTF, g_zKeyTime);
      if(zi >= 0) g_zones[zi].status = ZONE_INVALID;      // после стопа повторного входа в зону нет
     }
   string line = StringFormat("%s,%s,%s,%s,%s,%.1f,%s,%s,%s,%.2f,%.2f,%.2f,%s,%s",
                              TimeToString(tOut, TIME_DATE | TIME_MINUTES), dir > 0 ? "BUY" : "SELL",
                              g_pTrig, ClsName(g_pCls), g_pZoneTF, g_pZoneW,
                              DoubleToString(g_pEntry, g_digits), DoubleToString(g_pSL, g_digits),
                              DoubleToString(g_pTP, g_digits), g_pLot, pts, pnl, ex, g_pStrength);
   LogTrade(line);
   SendTG(StringFormat("%s %s закрыта (%s)\nВход: %s → Выход: %s\nРезультат: %+.1f pt / %+.2f $",
                       g_sym, dir > 0 ? "BUY" : "SELL", ex,
                       DoubleToString(inP, g_digits), DoubleToString(outP, g_digits), pts, pnl));
   g_pId = 0; g_exitTag = ""; g_closeRetry = 0;
   ClearLines();
   g_day = CalcDay();
   if(g_day.trades >= InpMaxTradesDay || g_day.pts >= InpDailyTarget || g_day.lossStreak >= InpMaxLossStreak)
      SendTG(StringFormat("Дневной лимит достигнут: сделок %d, %+.1f pt, стопов подряд %d",
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
      g_pId = id; g_pDir = d; g_pTrig = "RESTORED"; g_pCls = 0; g_pStrength = "-";
      g_pZoneTF = "-"; g_pZoneW = 0; g_pEntry = op; g_pSL = sl; g_pTP = tp; g_pLot = vol;
      g_pLal = 0; g_beDone = d * (sl - op) >= 0; g_partDone = false; g_exitTag = ""; g_closeRetry = 0;
     }
   if(FridayCloseTime()) { ClosePosNow(tk, "FRIDAY"); return; }

   double bid = SymbolInfoDouble(g_sym, SYMBOL_BID), ask = SymbolInfoDouble(g_sym, SYMBOL_ASK);
   double cur = d > 0 ? bid : ask;
   double profit = d * (cur - op);

   // безубыток
   if(!g_beDone && profit >= InpBEAt)
     {
      double nsl = NormalizeDouble(op + d * InpBEOffset, g_digits);
      if(d * (nsl - sl) > 0 && d * (cur - nsl) >= MinStopDist())
        {
         if(g_trade.PositionModify(tk, nsl, tp))
           {
            g_beDone = true;
            g_pSL = nsl;
            DrawLine("SL", nsl, clrRed);
            SendTG(StringFormat("%s %s: стоп в безубытке\nSL: %s", g_sym, d > 0 ? "BUY" : "SELL",
                                DoubleToString(nsl, g_digits)));
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
      RebuildZones();
     }

   UpdateZoneStates();

   datetime m15 = iTime(g_sym, PERIOD_M15, 0);
   if(m15 != g_lastM15)
     {
      g_lastM15 = m15;
      CheckEarlyExit();
     }

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
   ParseNews();
   if(Period() != PERIOD_M5)
      Print("Внимание: робот рассчитан на график M5.");

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)MathMax(1.0, MathRound(InpMaxSlip / g_point)));
   g_trade.SetTypeFillingBySymbol(g_sym);

   if(EffectiveRiskMode() != InpRiskMode)
      Print("Реальный счёт: режим риска ", EnumToString(InpRiskMode), " заменён на PERCENT.");

   ArrayResize(g_zones, 0);
   g_lastM5 = 0; g_lastM15 = 0; g_lastH1 = 0;
   g_day = CalcDay();
   ManagePosition();
   Print("Mr. Casino запущен: ", g_sym, ", риск ", EnumToString(EffectiveRiskMode()), ", магик ", InpMagic);
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
