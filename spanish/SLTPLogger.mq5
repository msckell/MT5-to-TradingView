//+------------------------------------------------------------------+
//| SLTPLogger.mq5                                                   |
//| Logs SL/TP changes per position_id to CSV (CommonData path).     |
//| Opens and closes file on each event to allow external cleanup.   |
//+------------------------------------------------------------------+
#property strict

input string INPUT_SYMBOL   = "XAUUSD";
input string INPUT_FILENAME = "sltp_log.csv";

//+------------------------------------------------------------------+
int OnInit()
  {
   Print("[SLTPLogger] OK. Loggeando en: ", INPUT_FILENAME,
         " | Symbol filter: ", INPUT_SYMBOL == "" ? "ALL" : INPUT_SYMBOL);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
  {
   if(trans.type != TRADE_TRANSACTION_POSITION &&
      trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;

   ulong position_id = trans.position;
   if(position_id == 0)
      return;

   string sym = trans.symbol;
   if(INPUT_SYMBOL != "" && sym != INPUT_SYMBOL)
      return;

   //--- Read live SL/TP from open position
   double sl = 0.0;
   double tp = 0.0;
   bool found = false;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_IDENTIFIER) == (long)position_id)
        {
         sl    = PositionGetDouble(POSITION_SL);
         tp    = PositionGetDouble(POSITION_TP);
         found = true;
         break;
        }
     }

   if(!found)
      return;  // position already closed, skip to avoid garbage

   //--- Open file, append, close (allows external readers/writers)
   int fh = FileOpen(INPUT_FILENAME,
                     FILE_WRITE|FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI, ',');
   if(fh == INVALID_HANDLE)
     {
      Print("[SLTPLogger] ERROR FileOpen: ", GetLastError());
      return;
     }
   FileSeek(fh, 0, SEEK_END);

   long ts_ms = (long)TimeCurrent() * 1000;
   FileWrite(fh,
             (string)position_id,
             sym,
             DoubleToString(sl, 5),
             DoubleToString(tp, 5),
             (string)ts_ms);
   FileClose(fh);

   Print("[SLTPLogger] pos=", position_id, " sl=", sl, " tp=", tp);
  }

//+------------------------------------------------------------------+
void OnTick() {}
//+------------------------------------------------------------------+
