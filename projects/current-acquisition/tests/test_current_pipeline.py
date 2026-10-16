"""Offline synthetic-frame and storage checks. Never opens a serial port."""
from pathlib import Path
import csv
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from serial_worker import SerialWorker
import data_store
import calibration_manager as cm
data_store.MAX_POINTS=16  # Small buffer tests the bound without allocating 408 MB.

def frame(seq=0,codes=None,kind=0xAA):
    return bytes([kind])+struct.pack('<8h',*(codes or [16384]*8))+bytes([seq])

class CurrentTests(unittest.TestCase):
    def test_zero_and_sensitivity(self):
        w=SerialWorker()
        w._process_data_frame(tuple([16384,16794]+[16384]*6))
        values=w._batch_buffer[0][1]
        self.assertEqual(len(values),16)
        self.assertEqual(values[0],2.5)
        self.assertEqual(values[1],0)
        self.assertAlmostEqual(values[3],(16794*5/32768-2.5)*16)

    def test_frame_fragmentation(self):
        for split in range(1,18):
            w=SerialWorker();raw=frame()
            w._buf.extend(raw[:split]);w._parse_frames()
            self.assertEqual(w._total_frames,0)
            w._buf.extend(raw[split:]);w._parse_frames()
            self.assertEqual(w._total_frames,1)

    def test_sequence_wrap_and_gap(self):
        w=SerialWorker();w._buf.extend(frame(255)+frame(0)+frame(3));w._parse_frames()
        self.assertEqual(w._total_frames,3)
        self.assertEqual(w._dropped_frames,2)

    def test_noise_prefix(self):
        w=SerialWorker();w._buf.extend(b'noise'+frame());w._parse_frames()
        self.assertEqual(w._total_frames,1)

    def test_calibration_decode(self):
        w=SerialWorker();received=[];w.cal_result.connect(received.append)
        w._buf.extend(frame(codes=[25000]*8,kind=0xBB));w._parse_frames()
        self.assertEqual(received,[[2.5]*8])

    def test_storage_bound_and_csv(self):
        ds=data_store.DataStore()
        for i in range(20):ds.add(1000+i*.001,[2.5,0.0]*8)
        self.assertEqual(ds.count(),16)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic.csv';self.assertEqual(ds.export_csv(str(p)),16)
            with p.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
            self.assertEqual(len(rows[0]),17)
            self.assertEqual(rows[0][1:3],['V1_V','I1_A'])

    def test_calibration_round_trip(self):
        old=cm.CAL_DIR
        try:
            with tempfile.TemporaryDirectory() as d:
                cm.CAL_DIR=d
                p=cm.save_calibration([2.5]*8,[0]*8,255,filename='synthetic')
                record=cm.load_calibration(p)
                self.assertEqual(record['vzero'],[2.5]*8)
                self.assertEqual(record['enabled_mask'],255)
        finally:cm.CAL_DIR=old

if __name__=='__main__':unittest.main(verbosity=2)
