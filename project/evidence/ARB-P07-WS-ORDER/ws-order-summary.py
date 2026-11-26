import json
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
assert len(rows) == 4
assert rows[-1] == {"socket_opens": 1, "socket_closes": 1}
for row in rows[:-1]:
    assert row['socket_opens_before'] == row['socket_opens_after'] == 1
    assert row['socket_closes_before'] == row['socket_closes_after'] == 0
    assert row['transport'] == 'websocket'
    assert row['dex_done'] <= row['kucoin_selection_start']
    assert row['source_skew_seconds'] <= 10 and row['receipt_skew_seconds'] <= 10
    dex, cex = row['observations']
    assert cex['id'] == row['book_version']
    assert datetime.fromisoformat(cex['received_at']) == datetime.fromisoformat(row['kucoin_receipt'])
    assert datetime.fromisoformat(dex['completed_at']) <= datetime.fromisoformat(row['kucoin_selection_start'])
    assert datetime.fromisoformat(row['ws_last_valid_message']) == datetime.fromisoformat(row['kucoin_receipt'])
    book = cex['kucoin_book']
    assert book['symbol'] == 'HBAR-USDT' and book['transport'] == 'websocket'
    bid, bq, ask, aq = (Fraction(book[key]) for key in ('bid_price', 'bid_quantity', 'ask_price', 'ask_quantity'))
    assert 0 < bid < ask and bq > 0 and aq > 0
    selected_during_dex = row['dex_start'] <= row['kucoin_receipt'] <= row['dex_done']
    reasons = [(item.get('status'), item.get('reason')) for item in row['evaluations']]
    valid = [item['units'] for item in row['gas']]
    print(row['cycle'], row['dex_start'], row['book_version'], selected_during_dex, row['source_skew_seconds'],
          row['receipt_skew_seconds'], str(bid), str(bq), str(ask), str(aq), valid, reasons)
