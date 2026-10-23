# Firmware r15f (mrf-r): cómo bajar del 95 % de CPU y ordenar el código

Fuente: <https://github.com/mrf-r/Neutron> (GPL-3, `neutr_cube/neutr_src`). Etiquetas: **[O]** leído en el código, **[I]** inferido (hay que medirlo).

## El problema, con números

- [O] La interrupción de audio `TIM6_DAC_IRQHandler` (`sr.c:71`) se ejecuta 48 000 veces por segundo y tiene **1000 ciclos** de presupuesto (TIM6 ARR = 999 a 48 MHz, `hal_init.c:106`).
- [O] Según su manual, el peor caso es el LFO en modo mono con sync externo: **947 de 1000 ciclos**.
- [O] El firmware ya mide su propio peor caso: guarda en `debug_sr_maxtime` los ciclos de TIM2 de cada interrupción (`sr.c:140-143`). **Esa es la herramienta de medida principal**, y ya está dentro del binario.

## Mejor forma de analizarlo

1. **Medir en el chip, no adivinar.** El Cortex-M0 no tiene contador de ciclos (DWT), pero TIM2 cuenta a 48 MHz y el código ya registra el máximo.
   - Hace falta leer `debug_sr_maxtime` y `debug_cr_maxtime` en cada modo de LFO. Hay dos vías:
     - **SWD con un ST-Link** (pines PA13/PA14). La carpeta ya trae la utilidad de ST-Link (`en.stsw-link004.zip`). Permite leer las variables en vivo sin tocar el firmware.
     - **Un volcado por MIDI DIN** añadido al firmware, por ejemplo un CC de petición que responda con un SysEx que lleve los máximos.
   - Tabla objetivo: ciclos máximos y medios × modo de LFO × sync interno/MIDI/externo.
2. **Arnés en el PC con salidas de referencia ("golden").** Compilar los módulos `neutr_lfo_*.c` para el PC, con los registros simulados. Se alimentan con las mismas entradas (potes, notas, sync) y se guarda la salida del DAC muestra a muestra. Cada optimización tiene que dar la **misma salida bit a bit**, o una diferencia acotada y justificada. Es el mismo método de esta app: tests sacados del comportamiento y verificación de que fallan.
3. **Coste estático por camino.** Compilar con `arm-none-eabi-gcc -O3` (las mismas opciones que su `subdir.mk`) y contar instrucciones del ISR y de cada `nl3m_srproc[...]` con `objdump`. Los tiempos del M0 son deterministas: 1 ciclo la ALU y el multiplicador rápido del F0, 2 cada load/store y 3 cada salto tomado. Tiene una advertencia: la flash a 48 MHz tiene 1 *wait state*, así que el número real sale del paso 1.

Hace falta instalar el **Arm GNU Toolchain** (no está en este PC) y, para el paso 1 por SWD, un **ST-Link**.

## Dónde se va la CPU: candidatos encontrados

| # | Qué | Dónde | Ahorro estimado | Riesgo |
|---|---|---|---|---|
| 1 | **Trazas de depuración siempre activas.** `global_debug()` se llama **3 veces por muestra** en el ISR. Cada llamada lee TIM7, escribe en un array `volatile` y avanza un índice. El código dice `//TODO delete this debug` | `sr.c:74,130,138`, `hal_init.c:53` | [I] ~40–60 ciclos/muestra (**4–6 %**, tanto como todo el margen actual) | Ninguno: compilarlo solo en builds de depuración (`#ifdef`) |
| 2 | **Formas de onda de ambos osciladores calculadas en cada muestra**, aunque cada CV solo sale 1 de cada 8 muestras (el DAC está multiplexado) | `sr.c:94-100` | [I] ~20–30 ciclos/muestra | Bajo: calcular solo el canal que va a salir (`timestamp & 7`) |
| 3 | **Variables `volatile` leídas varias veces dentro del ISR** (`adc_input`, `dac_output`, `adc_val`, `neutron_lfo`) | `sr.c:37-39,61` | [I] 5–15 ciclos | Bajo: copiar a variables locales una vez |
| 4 | **Lectura-modificación-escritura para limpiar el flag** `TIM6->SR &= ~UIF` | `sr.c:136` | [I] 2–4 ciclos | Ninguno: `TIM6->SR = ~TIM_SR_UIF` |
| 5 | **Código caliente en flash con wait state.** El ISR y las funciones `nl3m_*` se ejecutan desde flash | Linker script | [I] 10–25 % del camino caliente | Medio: hay que copiarlo a RAM (`.RamFunc`) y la RAM es de 16 KB; medirlo antes |
| 6 | **Divisiones con signo entre potencias de 2** (`/2`, `/65536`) en las macros de suavizado. GCC añade corrección de redondeo | `neutr_lfo_3mono.c:246-286`, `neutr_lfo_4poly.c:243-250` | [I] 2–3 ciclos por operación | Bajo: `>>` cambia el redondeo en 1 LSB; validarlo con el arnés golden |
| 7 | **Modo mono muestra a muestra.** El modo poly ya trabaja por bloques: un buffer de 64 muestras que se rellena fuera del ISR (`nl4p_sr` solo copia una muestra) | `neutr_lfo_3mono.c:922`, `neutr_lfo_4poly.c:401` | [I] el mayor ahorro: el ISR queda casi vacío en todos los modos | Alto: el hard sync externo necesita precisión de muestra; hay que guardar la marca de tiempo del flanco y aplicarla dentro del bloque |

Recomendación: aplicar **1 → 4** primero (cambios pequeños, cero riesgo audible), medir, y solo después atacar 5 y 7.

## Cómo dejarlo mejor organizado

Estado actual:
- [O] 8 349 líneas.
- [O] Unas 560 líneas comentadas.
- [O] Funciones `*_deprecated` todavía en el código.
- [O] Macros gigantes (`CODEBLOCK_*`, `REPEAT16`) en lugar de funciones.
- [O] Variables globales mezcladas con registros del hardware.

Su propio autor dice que hoy lo reescribiría.

Estructura propuesta para un fork:

```
firmware/
  hal/        registros, DMA, timers, ADC, DAC mux (lo único que toca hardware)
  dsp/        osciladores, suavizado, envolventes: C puro, sin registros, testeable en el PC
  modes/      un archivo por modo de LFO con la misma interfaz (tabla de funciones como hoy)
  ui/         páginas de menú y combinaciones de botones
  midi/       parser DIN (+ SysEx propio para configurar desde Neutron Manager)
  tests/      arnés del PC con salidas golden por modo
CMakeLists.txt + CI que compila para ARM y ejecuta los tests del PC
```

- Las macros `CODEBLOCK_*` pasan a ser funciones `static inline`. Con `-O3` cuestan lo mismo, y se pueden leer y probar.
- Se borran el código comentado y las funciones `*_deprecated`; git conserva el historial.
- La depuración se activa con un `#define` en lugar de estar siempre encendida.
- Un SysEx propio por DIN permitiría que esta app configure r15f y lea sus contadores de CPU. Así se cierra el círculo con la pestaña Firmware.

## Qué hace falta para empezar

1. Instalar el Arm GNU Toolchain.
2. Un ST-Link para leer los contadores en vivo (opcional si se añade el volcado por MIDI DIN).
3. Una copia completa del flash del Neutron (pestaña Firmware → Copia completa) antes de flashear cualquier build.
