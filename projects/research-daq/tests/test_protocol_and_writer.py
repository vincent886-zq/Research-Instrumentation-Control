"""Offline checks: synthetic frames only; never opens a hardware port."""
import importlib.util
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0,str(ROOT))
from 核心.f407_protocol import FrameParser, crc16_ccitt_false
from 核心.capture_writer import CaptureWriter

def frame(index=1):
    payload=b'\xA5\x5A\x01\x01\x06\x00'+struct.pack('<II6hI',index,index*100,0,1,-1,32767,-32768,100,0)
    return payload+struct.pack('<H',crc16_ccitt_false(payload))

class ProtocolTests(unittest.TestCase):
    def test_crc_reference(self):
        self.assertEqual(crc16_ccitt_false(b'123456789'),0x29B1)

    def test_valid_values(self):
        f=FrameParser().feed(frame())[0]
        self.assertEqual(f.sample_index,1)
        self.assertEqual(f.volts[4],-5.0)
        self.assertAlmostEqual(f.volts[3],32767*5/32768)

    def test_split_at_every_byte(self):
        data=frame()
        for offset in range(1,len(data)):
            p=FrameParser()
            self.assertEqual(p.feed(data[:offset]),[])
            self.assertEqual(len(p.feed(data[offset:])),1)

    def test_noise_and_multiple_frames(self):
        p=FrameParser()
        result=p.feed(b'noise'+frame(1)+frame(2))
        self.assertEqual([f.sample_index for f in result],[1,2])

    def test_bad_crc_resynchronizes(self):
        p=FrameParser();bad=bytearray(frame());bad[15]^=1
        self.assertEqual([f.sample_index for f in p.feed(bytes(bad)+frame(2))],[2])
        self.assertGreaterEqual(p.crc_errors,1)

    def test_wrong_header_rejected(self):
        p=FrameParser();bad=bytearray(frame());bad[4]=8
        self.assertEqual(len(p.feed(bytes(bad)+frame(2))),1)
        self.assertGreaterEqual(p.protocol_errors,1)

    def test_writer_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.csv'
            w=CaptureWriter(str(path),{'test':'synthetic'})
            f=FrameParser().feed(frame())[0]
            self.assertTrue(w.enqueue_raw(frame()))
            self.assertTrue(w.enqueue_samples([dict(time_s=.0001,sample_index=1,timestamp_us=100,
                device_drop_total=0,codes=f.codes,volts=f.volts)]))
            result=w.close({'raw_minimum_frame_bytes':32},True)
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['rows_written'],1)
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.with_suffix('.metadata.json').read_text())['capture_status'],'COMPLETE')

    def test_writer_invalid_keeps_part(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.csv'
            w=CaptureWriter(str(path),{})
            result=w.close({'raw_minimum_frame_bytes':32},True)
            self.assertFalse(result['capture_complete'])
            self.assertFalse(path.exists())
            self.assertTrue(Path(str(path)+'.part').exists())

if __name__=='__main__':
    unittest.main(verbosity=2)
