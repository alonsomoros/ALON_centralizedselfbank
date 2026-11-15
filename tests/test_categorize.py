"""Textos SINTÉTICOS con las mismas formas que Santander (docs/bancos.md). Nunca datos reales."""

import json
import tempfile
import unittest
from pathlib import Path

from selfbank import categorize as c
from selfbank.text import mask_sensitive, normalize, squash


class TextTest(unittest.TestCase):
    def test_normalizar(self):
        self.assertEqual(normalize("¿CUÁNTO gasté en AÑO?"), "¿cuanto gaste en ano?")
        self.assertEqual(squash("  Pull   &  Bear\tMadrid "), "pull & bear madrid")

    def test_enmascara_tarjeta_de_16_digitos(self):
        self.assertEqual(mask_sensitive("COMPRA TIENDA, ONLINE, TARJETA 4111111111111234 , COMISION 1,20"),
                         "COMPRA TIENDA, ONLINE, TARJETA XXXXXXXXXXXX1234 , COMISION 1,20")

    def test_enmascara_asterisco_y_seis_digitos(self):
        self.assertEqual(mask_sensitive("PAGO MOVIL EN TIENDA, MADRID, TARJ. :*123456"), "PAGO MOVIL EN TIENDA, MADRID, TARJ. :*XX3456")

    def test_no_toca_importes_ni_numeros_cortos(self):
        for text in ("COMISION 1,20 EUR", "BIZUM DE ANA CONCEPTO cena 25 euros", "PAGO EN BAR 24H, MADRID", "RECIBO 12345678"):
            self.assertEqual(mask_sensitive(text), text)

    def test_telefonos_y_referencias_largas(self):
        self.assertEqual(mask_sensitive("BIZUM A 600123456 DE ANA"), "BIZUM A XXXXX3456 DE ANA")

    def test_es_determinista_e_idempotente(self):
        text = "TARJETA 4111111111111234 y :*123456"
        once = mask_sensitive(text)
        self.assertEqual(mask_sensitive(text), once)
        self.assertEqual(mask_sensitive(once), once)

    def test_no_deja_digitos_de_tarjeta(self):
        import re
        out = mask_sensitive("A 4111111111111111 B :*654321 C 5500000000000004")
        self.assertIsNone(re.search(r"\d{9,}", out))
        self.assertNotIn("411111", out)


class ParseTest(unittest.TestCase):
    def test_compra_con_tarjeta(self):
        p = c.parse("PAGO MOVIL EN MERCADONA, MADRID, TARJ. :*XX3456")
        self.assertEqual((p.kind, p.merchant, p.category), ("purchase", "MERCADONA", "supermercado"))

    def test_compra_en_el_extranjero(self):
        p = c.parse("COMPRA ZARA HOME, LISBOA, TARJETA XXXXXXXXXXXX1234 , COMISION 1,20")
        self.assertEqual((p.kind, p.merchant, p.category), ("purchase", "ZARA HOME", "compras"))

    def test_categorias_por_comercio(self):
        cases = {
            "PAGO CON TARJETA EN GLOVO, BARCELONA, TARJ. :*XX1111": "restaurantes",
            "PAGO CON TARJETA EN UBER EATS, AMSTERDAM, TARJ. :*XX1111": "restaurantes",
            "PAGO CON TARJETA EN UBER TRIP, AMSTERDAM, TARJ. :*XX1111": "transporte",
            "PAGO CON TARJETA EN NETFLIX.COM, MADRID, TARJ. :*XX1111": "ocio_suscripciones",
            "PAGO CON TARJETA EN REPSOL, MADRID, TARJ. :*XX1111": "transporte",
            "PAGO CON TARJETA EN FARMACIA LOPEZ, MADRID, TARJ. :*XX1111": "salud",
            "PAGO CON TARJETA EN IBERDROLA CLIENTES, MADRID, TARJ. :*XX1111": "hogar_facturas",
            "PAGO CON TARJETA EN BOOKING.COM, AMSTERDAM, TARJ. :*XX1111": "viajes",
            "PAGO CON TARJETA EN AMAZON EU, LUXEMBURGO, TARJ. :*XX1111": "compras",
            "PAGO CON TARJETA EN CAJERO BANCO, MADRID, TARJ. :*XX1111": "efectivo",
            "PAGO CON TARJETA EN COMERCIO RARO SL, MADRID, TARJ. :*XX1111": "otros",
        }
        for text, category in cases.items():
            self.assertEqual(c.parse(text).category, category, text)

    def test_palabra_completa_dia_no_es_media(self):
        self.assertEqual(c.parse("PAGO CON TARJETA EN MEDIA MARKT, MADRID, TARJ. :*XX1111").category, "compras")
        self.assertEqual(c.parse("PAGO CON TARJETA EN DIA SUPERMERCADOS, MADRID, TARJ. :*XX1111").category, "supermercado")
        self.assertEqual(c.parse("PAGO CON TARJETA EN COMEDIA ESTUDIO, MADRID, TARJ. :*XX1111").category, "otros")

    def test_compra_en_el_extranjero_no_es_una_comision(self):
        # Regresión: «, TARJETA XXXX , COMISION 1,20» va al final de TODA compra en el extranjero (cambio de divisa).
        for text in ("COMPRA RARO SL, PARIS, TARJETA XXXXXXXXXXXX1234 , COMISION 1,20", "COMPRA OTRO NEGOCIO SL, ROMA, TARJETA XXXXXXXXXXXX9999 , COMISION 0,45"):
            p = c.parse(text)
            self.assertEqual((p.kind, p.category), ("purchase", "otros"), text)
        self.assertEqual(c.parse("COMPRA ZARA HOME, LISBOA, TARJETA XXXXXXXXXXXX1234 , COMISION 1,20").category, "compras")

    def test_la_comision_real_sigue_siendo_comision(self):
        self.assertEqual(c.parse("COMISION MANTENIMIENTO CUENTA").category, "comisiones")
        self.assertEqual(c.parse("COMISION POR DESCUBIERTO").kind, "fee")

    def test_los_datos_de_tarjeta_no_cuentan_para_categorizar(self):
        self.assertEqual(c.parse("PAGO MOVIL EN COMERCIO RARO, MADRID, TARJ. :*XX3456").category, "otros")

    def test_bizum_recibido(self):
        p = c.parse("BIZUM DE ANA GARCIA LOPEZ CONCEPTO cena")
        self.assertEqual((p.kind, p.merchant, p.category), ("bizum", "ANA GARCIA LOPEZ", "bizum"))

    def test_bizum_enviado(self):
        p = c.parse("BIZUM A LUIS DE MARTA RUIZ SOTO CONCEPTO: regalo")
        self.assertEqual((p.kind, p.merchant), ("bizum", "LUIS"))
        self.assertEqual(c.parse("BIZUM A LUIS CONCEPTO: regalo").merchant, "LUIS")

    def test_bizum_raro_no_rompe(self):
        p = c.parse("BIZUM")
        self.assertEqual((p.kind, p.merchant, p.category), ("bizum", None, "bizum"))

    def test_transferencia(self):
        p = c.parse("TRANSFERENCIA DE EMPRESA EJEMPLO SL, CONCEPTO NOMINA NOVIEMBRE")
        self.assertEqual((p.kind, p.merchant, p.category), ("transfer", "EMPRESA EJEMPLO SL", "transferencias"))

    def test_devolucion_y_anulacion(self):
        p = c.parse("ANUL PAGO EN ZARA, MADRID, TARJ. :*XX1111")
        self.assertEqual((p.kind, p.merchant, p.category), ("refund", "ZARA", "compras"))
        self.assertEqual(c.parse("DEVOLUCION RECIBO LUZ").kind, "refund")
        self.assertEqual(c.parse("RETROCESIÓN COMPRA").kind, "refund")

    def test_ingreso_y_comision(self):
        self.assertEqual(c.parse("INGRESO EN EFECTIVO").kind, "income")
        p = c.parse("COMISION MANTENIMIENTO CUENTA")
        self.assertEqual((p.kind, p.category), ("fee", "comisiones"))

    def test_texto_desconocido(self):
        p = c.parse("ALGO COMPLETAMENTE RARO 123")
        self.assertEqual((p.kind, p.merchant, p.category), ("other", "ALGO COMPLETAMENTE RARO", "otros"))

    def test_recibo_sin_comercio_se_identifica_por_sus_palabras(self):
        a, b = c.parse("RECIBO ALQUILER VIVIENDA 0123 NOV"), c.parse("RECIBO ALQUILER VIVIENDA 0456 DIC")
        self.assertEqual((a.merchant, a.category), ("RECIBO ALQUILER VIVIENDA NOV", "hogar_facturas"))
        self.assertEqual(c.parse("RECIBO ALQUILER VIVIENDA 0123").merchant, "RECIBO ALQUILER VIVIENDA")
        self.assertEqual(b.category, "hogar_facturas")

    def test_no_lanza_con_entradas_extremas(self):
        for text in ("", " ", "PAGO", "PAGO EN ,", "BIZUM DE", "TRANSFERENCIA", "x" * 500, "COMPRA , ,"):
            c.parse(text)


class OverridesTest(unittest.TestCase):
    def test_reglas_del_usuario(self):
        cat = c.Categorizer({"viajes": ["cafeteria del puerto"], "mascotas": ["kiwoko"]})
        self.assertEqual(cat.category("CAFETERIA DEL PUERTO"), "viajes")  # las reglas del usuario mandan sobre las de por defecto
        self.assertEqual(cat.category("CAFETERIA OTRA"), "restaurantes")  # lo demás sigue igual
        self.assertEqual(cat.category("KIWOKO MADRID"), "mascotas")
        self.assertEqual(c.parse("PAGO CON TARJETA EN KIWOKO, MADRID, TARJ. :*XX1111", cat).category, "mascotas")

    def test_desde_fichero_y_sin_fichero(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "categories.local.json"
            path.write_text(json.dumps({"mascotas": ["kiwoko"]}), encoding="utf-8")
            self.assertEqual(c.Categorizer.from_file(path).category("kiwoko"), "mascotas")
            self.assertEqual(c.Categorizer.from_file(Path(folder) / "no-existe.json").category("kiwoko"), "otros")

    def test_las_categorias_por_defecto_son_las_del_contrato(self):
        from selfbank import contract
        schema = contract.load_schema(contract.MODEL_SCHEMA_PATH)
        allowed = set(schema["properties"]["category"]["enum"])
        self.assertTrue(set(c.DEFAULT_RULES) <= allowed)


if __name__ == "__main__":
    unittest.main()
