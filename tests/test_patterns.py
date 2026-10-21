from helpers import mk
from xau_engine.engine.params import Params
from xau_engine.engine.patterns import (
    detect_fu,
    detect_negation,
    find_order_blocks,
    find_retests,
    is_doji,
    is_doji_not_afu,
)
from xau_engine.models import Candle, Side

P = Params()


def test_doji_small_body_and_inside_body():
    c = mk([(100, 103, 97, 100.2), (100, 105, 95, 104), (100.5, 101, 100, 100.8)])
    assert is_doji(c, 0, P)          # тело 0.2 из 6
    assert not is_doji(c, 1, P)      # крупное тело
    assert is_doji(c, 2, P)          # внутри тела следующей/предыдущей
    assert not is_doji_not_afu(mk([(100, 101, 99, 100.0), (100, 102, 98, 100.0)]), 1, P)  # берёт ликвидность


def test_sfu_biw_is_rank1_sell():
    a = Candle(0, 106, 110, 100, 107)
    b = Candle(300, 105.5, 111, 100.5, 100.8)
    fu = detect_fu([a, b], 1, Side.SELL, P)
    assert fu and fu.rank == 1 and fu.strong and fu.biw
    assert fu.tip == 111 and fu.ref == 110


def test_sfu_engulf_is_rank2():
    a = Candle(0, 100, 105, 99, 104)
    b = Candle(300, 104.5, 108, 99, 99.5)
    fu = detect_fu([a, b], 1, Side.SELL, P)
    assert fu and fu.rank == 2 and fu.strong


def test_weak_biw_is_rank7():
    a = Candle(0, 106, 110, 100, 107)
    b = Candle(300, 104, 110.5, 90, 104.5)
    fu = detect_fu([a, b], 1, Side.SELL, P)
    assert fu and fu.rank == 7 and not fu.strong


def test_bullish_fu_is_mirror_of_bearish():
    a = Candle(0, 106, 110, 100, 107)
    b = Candle(300, 105.5, 111, 100.5, 100.8)
    bear = detect_fu([a, b], 1, Side.SELL, P)
    bull = detect_fu([a.mirrored(), b.mirrored()], 1, Side.BUY, P)
    assert bull and bull.rank == bear.rank
    assert bull.tip == -bear.tip and bull.ref == -bear.ref
    assert bull.wick_lo < bull.wick_hi


def test_no_fu_without_sweep_or_close_back():
    a = Candle(0, 100, 105, 99, 104)
    assert detect_fu([a, Candle(300, 104, 104.8, 100, 101)], 1, Side.SELL, P) is None  # не забрал хай
    assert detect_fu([a, Candle(300, 104, 108, 103, 107)], 1, Side.SELL, P) is None    # не вернулся


def test_negation_bear_to_bull_strong():
    rows = [
        (100, 102, 99, 101),
        (101, 101.2, 97, 97.1),   # медвежья забирает низ почти без нижней тени
        (97, 102, 92, 101.5),     # бычья: поглощение + большая нижняя тень
    ]
    n = detect_negation(mk(rows), 2, Side.BUY, P)
    assert n and n.strong and n.side is Side.BUY and n.level == 97 and n.tip == 92
    assert detect_negation(mk(rows), 2, Side.SELL, P) is None


def test_negation_bull_to_bear_is_mirror():
    # зеркальные цены: строим как mirrored от бычьего случая
    base = mk([(100, 102, 99, 101), (101, 101.2, 97, 97.1), (97, 102, 92, 101.5)])
    sell = [c.mirrored() for c in base]
    n = detect_negation(sell, 2, Side.SELL, P)
    assert n and n.strong and n.side is Side.SELL


def test_order_block_sell_to_buy_with_fu():
    rows = [
        (100, 101, 99.5, 100.8),  # движение вверх
        (100.8, 101.5, 100.7, 101.4),
        (101.4, 101.5, 100.5, 100.6),  # odd: медвежья почти без теней (k=2)
        (100.6, 104, 100.2, 103.8),   # бычья: забирает low odd (FU) и закрывается выше
        (103.8, 106, 103.5, 105.8),
        (105.8, 108, 105.5, 107.5),
    ]
    obs = find_order_blocks(mk(rows), "H1", P, lookback=10)
    assert len(obs) == 1
    ob = obs[0]
    assert ob.side is Side.BUY and ob.label == "Sell to Buy"
    assert (ob.lo, ob.hi) == (100.6, 101.4)  # POI — только тело
    assert ob.fu.side is Side.BUY


def test_order_block_dropped_when_closed_below_zone():
    rows = [
        (100, 101, 99.5, 100.8),
        (100.8, 101.5, 100.7, 101.4),
        (101.4, 101.5, 100.5, 100.6),
        (100.6, 104, 100.2, 103.8),
        (103.8, 106, 103.5, 105.8),
        (105.8, 108, 105.5, 107.5),
        (107.5, 107.6, 99.0, 99.5),  # закрытие ниже тела OB
    ]
    assert find_order_blocks(mk(rows), "H1", P, lookback=10) == []


def test_hcs_is_fu_retested_with_fu():
    # якорный бычий FU: длинная нижняя тень
    rows = [
        (110, 111, 105, 106),
        (106, 107, 100, 106.5),       # i=1 FU вниз к 100, тень 100..106
        (106.5, 112, 106.2, 111.5),   # ушли вверх (low > верха тени 106)
        (111.5, 113, 110.5, 112.5),
        (112.5, 113, 104, 112.8),     # возврат в тень: low 104 (FU по отношению к предыдущей), закрытие выше
    ]
    c = mk(rows)
    anchor = detect_fu(c, 1, Side.BUY, P)
    assert anchor is not None
    ev = find_retests(c, anchor, P)
    assert len(ev) == 1 and ev[0].kind == "HCS" and ev[0].fu is not None


def test_plain_retest_without_fu():
    rows = [
        (110, 111, 105, 106),
        (106, 107, 100, 106.5),
        (106.5, 112, 106.2, 111.5),
        (111.5, 111.8, 105.9, 105.5),  # дрейф в тень, закрытие внутри неё: событий нет
        (105.5, 108, 105.9, 107.5),    # low не ниже предыдущей (не FU), закрытие выше тени: обычный ретест
    ]
    c = mk(rows)
    anchor = detect_fu(c, 1, Side.BUY, P)
    ev = find_retests(c, anchor, P)
    assert len(ev) == 1 and ev[0].kind == "RETEST"
