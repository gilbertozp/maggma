"""
Test lava.diff module
"""

import os
import logging
import random
import unittest
import json

from maggma.lava.diff import Differ, Delta
from maggma.helpers import get_database

__author__ = 'Dan Gunter <dkgunter@lbl.gov>'


module_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
db_dir = os.path.abspath(os.path.join(module_dir, "..", "..", "..", "test_files", "settings_files"))


def recname(num):
    return 'item-{:d}'.format(num)


def create_record(num):
    return {
        'name': recname(num),
        'color': random.choice(("red", "orange", "green", "indigo", "taupe", "mauve")),
        'same': 'yawn',
        'idlist': list(range(num)),
        'zero': 0,
        'energy': random.random() * 5 - 2.5,
    }


class MyTestCase(unittest.TestCase):
    NUM_RECORDS = 10

    @classmethod
    def setUpClass(cls):
        mg = logging.getLogger("mg")
        mg.setLevel(logging.ERROR)
        mg.addHandler(logging.StreamHandler())

    def setUp(self):
        self.collections_names, self.collections = ['diff1', 'diff2'], []
        self.colors = [[None, None] for _ in range(self.NUM_RECORDS)]
        self.energies = [[None, None] for _ in range(self.NUM_RECORDS)]
        with open(os.path.join(db_dir, "db.json"), "r") as f:
            creds_dict = json.load(f)
        db = get_database(creds_dict)
        for c in self.collections_names:
            coll = db[c]
            self.collections.append(coll)
        for ei, coll in enumerate(self.collections):
            coll.remove({})
            for i in range(self.NUM_RECORDS):
                rec = create_record(i)
                coll.insert(rec)
                # save some vars for easy double-checking
                self.colors[i][ei] = rec['color']
                self.energies[i][ei] = rec['energy']

    def test_key_same(self):
        """Keys only and all keys are the same.
        """
        # Perform diff.
        df = Differ(key='name')
        d = df.diff(*self.collections)
        # Check results.
        self.assertEqual(len(d[Differ.NEW]), 0)
        self.assertEqual(len(d[Differ.MISSING]), 0)

    def test_key_different(self):
        """Keys only and keys are different.
        """
        # Add one different record to each collection.
        self.collections[0].insert(create_record(self.NUM_RECORDS + 1))
        self.collections[1].insert(create_record(self.NUM_RECORDS + 2))
        # Perform diff.
        df = Differ(key='name')
        d = df.diff(*self.collections)
        # Check results.
        self.assertEqual(len(d[Differ.MISSING]), 1)
        self.assertEqual(d[Differ.MISSING][0]['name'], recname(self.NUM_RECORDS + 1))
        self.assertEqual(len(d[Differ.NEW]), 1)
        self.assertEqual(d[Differ.NEW][0]['name'], recname(self.NUM_RECORDS + 2))

    def test_eqprops_same(self):
        """Keys and props, all are the same.
        """
        # Perform diff.
        df = Differ(key='name', props=['same'])
        d = df.diff(*self.collections)
        # Check results.
        self.assertEqual(len(d[Differ.CHANGED]), 0)

    def test_eqprops_different(self):
        """Keys and props, some props out of range.
        """
        # Perform diff.
        df = Differ(key='name', props=['color'])
        d = df.diff(*self.collections)
        # Calculate expected results.
        changed = sum((int(c[0] != c[1]) for c in self.colors))
        # Check results.
        self.assertEqual(len(d[Differ.CHANGED]), changed)

    def test_numprops_same(self):
        """Keys and props, all are the same.
        """
        # Perform diff.
        df = Differ(key='name', deltas={"zero": Delta("+-0.001")})
        d = df.diff(*self.collections)
        # Check results.
        self.assertEqual(len(d[Differ.CHANGED]), 0)

    def test_numprops_different(self):
        """Keys and props, some props different.
        """
        # Perform diff.
        delta = 0.5
        df = Differ(key='name', deltas={"energy": Delta("+-{:f}".format(delta))})
        d = df.diff(*self.collections)
        # Calculate expected results.
        is_different = lambda a, b: abs(a - b) > delta
        changed = sum((int(is_different(e[0], e[1])) for e in self.energies))
        # Check results.
        self.assertEqual(len(d[Differ.CHANGED]), changed)

    def test_numprops_different_sign(self):
        """Keys and props, some props different.
        """
        # Perform diff.
        df = Differ(key='name', deltas={"energy": Delta("+-")})
        d = df.diff(*self.collections)
        # Calculate expected results.
        is_different = lambda a, b: a < 0 < b or b < 0 < a
        changed = sum((int(is_different(e[0], e[1])) for e in self.energies))
        # Check results.
        self.assertEqual(len(d[Differ.CHANGED]), changed)

    def test_numprops_different_pct(self):
        """Keys and props, some props different, check pct change.
        """
        # Perform diff.
        minus, plus = 10, 20
        df = Differ(key='name', deltas={"energy": Delta("+{}-{}=%".format(plus, minus))})
        d = df.diff(*self.collections)

        # Calculate expected results.
        def is_different(a, b):
            pct = 100.0 * (b - a) / a
            return pct <= -minus or pct >= plus
        changed = sum((int(is_different(e[0], e[1])) for e in self.energies))

        # Check results.
        if len(d[Differ.CHANGED]) != changed:
            result = d[Differ.CHANGED]
            msg = "Values:\n"
            for i, e in enumerate(self.energies):
                if not is_different(*e):
                    continue
                msg += "{:d}) {:f} {:f}\n".format(i, e[0], e[1])
            msg += "Result:\n"
            for i, r in enumerate(result):
                msg += "{:d}) {} {}\n".format(i, r["old"], r["new"])
            self.assertEqual(len(d[Differ.CHANGED]), changed, msg=msg)

    # repeat this test a few more times
    test_numprops_different_pct1 = test_numprops_different_pct
    test_numprops_different_pct2 = test_numprops_different_pct
    test_numprops_different_pct3 = test_numprops_different_pct

    def test_delta(self):
        """Delta class parsing.
        """
        self.failUnlessRaises(ValueError, Delta, "foo")

    def test_delta_sign(self):
        """Delta class sign.
        """
        d = Delta("+-")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(-1, 0), False)
        self.assertEquals(d.cmp(-1, 1), True)

    def test_delta_val_sa(self):
        """Delta class value, same absolute.
        """
        d = Delta("+-3")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(1, 4), False)
        self.assertEquals(d.cmp(1, 5), True)

    def test_delta_val_da(self):
        """Delta class value, different absolute.
        """
        d = Delta("+2.5-1.5")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(1, 3), False)
        self.assertEquals(d.cmp(3, 1), True)

    def test_delta_val_sae(self):
        """Delta class value, same absolute equality.
        """
        d = Delta("+-3.0=")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(1, 4), True)
        self.assertEquals(d.cmp(4, 1), True)

    def test_delta_val_sp(self):
        """Delta class value, same percentage.
        """
        d = Delta("+-25%")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(8, 4), True)
        self.assertEquals(d.cmp(8, 6), False)

    def test_delta_val_spe(self):
        """Delta class value, same percentage equality.
        """
        d = Delta("+-25=%")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(8, 4), True)
        self.assertEquals(d.cmp(8, 6), True)

    def test_delta_val_dpe(self):
        """Delta class value, different percentage equality.
        """
        d = Delta("+50-25=%")
        self.assertEquals(d.cmp(0, 1), False)
        self.assertEquals(d.cmp(8, 4), True)
        self.assertEquals(d.cmp(8, 6), True)
        self.assertEquals(d.cmp(6, 8), False)
        self.assertEquals(d.cmp(6, 9), True)

    def test_delta_plus(self):
        """Delta class value 'plus only'.
        """
        d = Delta("+50")
        self.assertEquals(d.cmp(0, 50), False)
        self.assertEquals(d.cmp(0, 51), True)
        self.assertEquals(d.cmp(10, 5), False)
        d = Delta("+50=")
        self.assertEquals(d.cmp(0, 50), True)
        d = Delta("+50%")
        self.assertEquals(d.cmp(10, 25), True)
        self.assertEquals(d.cmp(25, 10), False)

    def test_delta_minus(self):
        """Delta class value 'minus only'.
        """
        d = Delta("-50")
        self.assertEquals(d.cmp(0, 50), False)
        self.assertEquals(d.cmp(51, 0), True)
        self.assertEquals(d.cmp(5, 10), False)
        d = Delta("-50=")
        self.assertEquals(d.cmp(50, 0), True)
        d = Delta("-50%")
        self.assertEquals(d.cmp(25, 10), True)
        self.assertEquals(d.cmp(10, 25), False)

if __name__ == '__main__':
    unittest.main()
