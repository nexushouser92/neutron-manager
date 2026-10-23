# ¿Hasta dónde se puede llevar el Neutron?

Fuentes: el código GPL de mrf-r (`hal_init.c`, `neutron.h`, `midi.c`, `nvm.c`, el linker script), su `manual.txt` y el binario `r15f_08000000.bin`. Etiquetas: **[O]** observado, **[I]** inferido.

## Qué controla de verdad el microcontrolador

El Neutron es un synth **analógico** con un cerebro digital pequeño. El micro no genera el sonido principal: lo **dirige**.

| Recurso | Dato | Fuente |
|---|---|---|
| MCU | STM32F072CB: Cortex-M0 a 48 MHz, **128 KB de flash, 16 KB de RAM**, USB Full-Speed | [O] linker `STM32F072CBTx_FLASH.ld` |
| Muestreo | 48 kHz con **1000 ciclos de CPU por muestra** | [O] TIM6 ARR = 999 |
| Carga de r15f | Pico de 947 de 1000 ciclos, es decir, **~5 % libre** | [O] `manual.txt` |
| Flash usada por r15f | 78 436 B (59,8 %), quedan **~49 KB libres** | [O] tamaño del .bin |
| DAC canal 1 | **Un solo DAC de 12 bits multiplexado a 8 CV** (sample & hold): afinado fino ×2, grueso ×2, cutoff del VCF, salida Assign, forma OSC1, forma OSC2 | [O] `neutron.h` (`cvch_en`), pines `cv mult sel0-2` |
| DAC canal 2 | **Salida del LFO directa a 48 kHz y 12 bits** | [O] `hal_init.c` |
| ADC | Solo **5 potenciómetros**: forma OSC1, forma OSC2, forma LFO, velocidad LFO, glide | [O] `adcch_en` |
| Botones / LEDs | 74HC165 (entrada) / 74HC595 (salida, PWM de 5 bits) por SPI | [O] mapa de pines |
| Autoafinado | 2 contadores de frecuencia por hardware (TIM1/TIM17) que miden los VCO | [O] `hal_init.c` |
| MIDI | DIN por USART1 a 31 250 baudios | [O] BRR = 0x600 a 48 MHz |
| USB | El periférico existe y se habilita, pero r15f **no** implementa USB-MIDI | [O] RCC USBEN / [I] sin descriptores |

Nota: el README de mrf-r dice "stm32f051". Lo contradicen el linker script y el tamaño del binario, que supera los 64 KB que tiene como máximo un F051. Es un F072.

## Límites físicos (no se arreglan con software)

1. **No puede ser polifónico de verdad.** Hay 2 VCO, 1 VCF y 1 VCA analógicos, así que el máximo es paráfono a 2 notas.
2. **No puede guardar patches completos.** Filtro, envolventes, mezclador y la mayoría de mandos son analógicos y el micro **no los lee**; solo ve 5 potenciómetros. Los "presets" solo pueden cubrir modos, afinación y LFO.
3. **No hay FM a velocidad de audio desde el micro sobre los VCO.** Las 8 CV comparten un DAC multiplexado: el pitch se refresca a unos 3 kHz y la forma a 6 kHz. La FM de audio solo se consigue con cables en el patchbay.
4. **Casi no queda CPU.** Cualquier función nueva compite con ese ~5 % libre. Para meter algo grande hay que quitar o reescribir otra cosa.

## Lo que sí se puede exprimir

| Idea | Viabilidad | Dónde se haría |
|---|---|---|
| **El LFO como tercer oscilador digital.** La salida del LFO es un canal de audio de 48 kHz y 12 bits: wavetables, ruido, sample & hold, osciladores digitales extra. r15f ya hace oscilador mono y poly de 5 voces por ahí | ✅ demostrado por r15f | Firmware |
| **Wavetables subidas desde el PC** (unas 40 tablas de 256 muestras de 12 bits caben en ~20 KB) | 🟡 posible; necesita un canal de datos (ver la siguiente fila) | Firmware + app |
| **Añadir USB-MIDI a mrf-r.** Así la app de PC podría configurar r15f por SysEx en lugar de con combinaciones de botones | 🟡 posible pero difícil: el stack USB cuesta CPU y flash, y el margen es del 5 % | Fork del firmware (GPL) |
| **Editor por MIDI DIN para r15f** (con un interfaz USB-MIDI del PC al DIN del Neutron) | 🟡 primero hay que añadir SysEx a r15f | Firmware + app |
| **Autoafinado y calibración guiados desde el PC** | ✅ el hardware de medición existe | Firmware ya lo tiene; la app guía |
| **Volver al firmware de fábrica de forma segura** (backup y restauración de los 128 KB) | ✅ el bootloader ROM no se puede borrar | **App (fase 1)** |
| **Editar los ajustes de fábrica por SysEx** (los 37 parámetros de SPEC §5) | ✅ protocolo mapeado | **App (fase 1)** |

## Propuesta por fases

- **Fase 1, solo la app (sin riesgo para el hardware):** editor SysEx del firmware de fábrica, flasheo DFU de r15f, backup y restauración, y guía interactiva de las combinaciones de r15f.
- **Fase 2, fork del firmware GPL:** añadir SysEx por DIN o USB a r15f para que la app lo configure, y subida de wavetables al LFO-oscilador.
- **Fase 3:** presets de "modo y LFO" guardados en el PC.
