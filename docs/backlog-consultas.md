# Backlog de consultas (fuera de la Fase 0)

Preguntas reales del usuario que el contrato v2 **no puede expresar todavía**. Hoy darían `unknown` (`fuera_de_alcance`). Cada una necesita una extensión pequeña y determinista; ninguna exige que el modelo razone sobre los datos.

| Pregunta | Extensión propuesta | Dificultad |
| :--- | :--- | :--- |
| ¿Cuánto gasto **de media al mes** en gasolina? | `metric: avg_month` (suma del periodo / meses del periodo). Regla: «de media», «al mes de media», «promedio» | Baja |
| ¿Cuántos pedidos de **Glovo o Uber Eats**…? | `text` con alternativas separadas por `\|` (`Glovo\|Uber Eats`); el ejecutor busca cualquiera. Añadir al prompt | Baja |
| ¿A qué tienda voy **más veces**? | `top_merchants` admite `metric: count` (por defecto `sum`). Regla: «más veces», «con más frecuencia» | Baja |
| ¿Gasto más los **fines de semana** o entre semana? | `group_by: weekday_type` (laborable / fin de semana) | Media |
| ¿Tengo algún **cargo duplicado** este mes? | Intent `duplicates`: mismo importe y comercio en ≤ N días | Media |
| ¿Me ha **subido alguna suscripción**? | Intent `price_changes`: cargos recurrentes cuyo importe aumentó entre ciclos | Media |
| Reparto 25/15/50/10 y lo que queda este mes | Intent `budget` con cubos configurados por el usuario | Media |
| Ingresado el mes pasado frente a gastado hasta ahora | Dos agregados y un cociente (intent `ratio` o plantilla propia) | Media |
| Patrones de gasto, dónde ahorrar | **No** en el diseño actual (exigiría razonar sobre los datos) | — |

Notas del ejecutor (no del contrato):
- **Coincidencia aproximada de comercios**: «burguer king» debe encontrar «BURGER KING». Normalizar (minúsculas, sin tildes) y comparar por trigramas contra los comercios reales, nunca por lo que escriba el modelo.
- **Personas en Bizum**: comprobar con datos reales si el texto del Bizum incluye el nombre del destinatario antes de prometer «Bizums que le envié a X».
- `group_by: merchant` también sirve para «por concepto» en ingresos (la contraparte o el concepto).
