"""Pruebas de remittance_shape con textos inventados."""

import unittest

import remittance_shape as rs

MERCHANTS = ["MERCADONA", "NETFLIX", "ZARA", "CAFETERIA LUNA", "GASOLINERA REPSOL"]


def tx(text, sign="DBIT"):
    return {"credit_debit_indicator": sign, "remittance_information": [text] if text is not None else None}


class ShapeTest(unittest.TestCase):
    def test_palabra_desconocida_pasa_a_A(self):
        self.assertEqual(rs.shape("MERCADONA"), "A")

    def test_varias_palabras_se_colapsan(self):
        self.assertEqual(rs.shape("CAFETERIA LUNA CENTRO"), "A+")

    def test_terminos_genericos_se_mantienen(self):
        self.assertEqual(rs.shape("Compra con tarjeta en NETFLIX"), "COMPRA CON TARJETA EN A")

    def test_acentos_no_rompen_el_termino(self):
        self.assertEqual(rs.shape("Domiciliación recibo"), "DOMICILIACION RECIBO")

    def test_numeros_solo_conservan_longitud(self):
        self.assertEqual(rs.shape("Tarjeta 1234 el 12/05/2026"), "TARJETA 9{4} EL 9{2}/9{2}/9{4}")

    def test_alfanumerico_se_separa(self):
        self.assertEqual(rs.shape("AB12"), "A9{2}")

    def test_vacio(self):
        self.assertEqual(rs.shape(""), "")


class ReportTest(unittest.TestCase):
    def sample(self):
        data = [tx(f"COMPRA TARJ {m} 1234") for m in MERCHANTS for _ in range(6)]
        data += [tx("BIZUM de Maria Lopez Garcia", "CRDT") for _ in range(7)]
        data += [tx("Concepto raro unico 998877")]  # forma única: debe ocultarse
        data += [tx(None), tx("")]
        return data

    def test_nunca_aparece_un_comercio_ni_un_nombre(self):
        report = rs.build_report(self.sample(), k=5)
        for secret in MERCHANTS + ["MARIA", "LOPEZ", "GARCIA", "RARO", "998877", "1234"]:
            self.assertNotIn(secret, report.upper())

    def test_muestra_la_forma_frecuente(self):
        report = rs.build_report(self.sample(), k=5)
        self.assertIn("COMPRA TARJ A 9{4}", report)
        self.assertIn("BIZUM DE A+", report)

    def test_oculta_formas_raras(self):
        report = rs.build_report(self.sample(), k=5)
        self.assertNotIn("9{6}", report)
        self.assertIn("ocultas por privacidad", report)

    def test_cuenta_sin_concepto(self):
        report = rs.build_report([tx(None)] * 6, k=5)
        self.assertIn("(sin concepto)", report)


if __name__ == "__main__":
    unittest.main()
