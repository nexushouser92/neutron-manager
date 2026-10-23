# SPEC — Neutron Manager

Esta spec describe **comportamiento observado** de dos fuentes:

1. **Neutron Setup 1.0.3** (Behringer, propietario). Se estudió en sala limpia, sin ejecutarlo; solo se extrajeron los archivos. Aquí solo se registran los valores de interfaz (el formato del mensaje en el cable), que hacen falta para interoperar. No hay código, nombres internos ni textos de UI copiados de la app.
2. **Firmware alternativo mrf-r r15f** (GPL-3, <https://github.com/mrf-r/Neutron>). El código es abierto y se puede reutilizar respetando la GPL.

Cada afirmación lleva una etiqueta: **[O]** observado (con Evidence), **[I]** inferido (con confianza) o **[?]** desconocido (con el ID de unknown en REA).

Evidence principal: `ev_a208f4907d43e9f63464fc1544a93109c77c920afd36074fd5a78bff1777034f` (`analyze_javascript_application` sobre `app.asar` → `build/render.js`, `build/main.js`).

---

## 1. Arquitectura de Neutron Setup

- [O] Instalador NSIS que contiene una app Electron (`app-64.7z` → `resources/app.asar`). Evidence `ev_caf34d06…` (PE sin contenedores para REA) y la extracción con 7-Zip.
- [O] El proceso principal solo crea **una ventana** de 960×800 (mínimo 490×480) y bloquea una segunda instancia. No hay preload, IPC ni procesos hijo.
- [O] Toda la lógica corre en el renderer y habla con el synth mediante **Web MIDI con SysEx activado**.
- [O] **La app no flashea firmware.** Solo es un editor de ajustes globales. Se limita a comprobar que la versión de firmware sea al menos la mínima exigida.

## 2. Detección del puerto

- [O] Un puerto MIDI cuenta como Neutron si su nombre es exactamente `Neutron(N)`, con N de 1 a 2 dígitos, y, cuando el puerto informa fabricante, este es `Behringer`.
- [O] Conexión automática: en cuanto aparecen entrada y salida que cumplen eso, se seleccionan las primeras de cada lista. Tras un cambio en la lista de dispositivos hay un *debounce* de unos 10 ms.
- [O] Cada vez que cambia la lista de dispositivos, el estado vuelve a "desconectado".

## 3. Trama SysEx

```
F0  00 20 32  28  7F  <opcode>  <datos...>  F7
│   └ fabricante (3 bytes)
│              └ familia
│                  └ dispositivo (7F = todos)
```

- [O] Byte 0 = `F0`; bytes 1–3 = ID de fabricante `00 20 32`; byte 4 = familia `0x28`; byte 5 = `0x7F` al enviar; byte 6 = opcode; el último byte = `F7`.
- [O] Un mensaje entrante se acepta solo si empieza en F0, termina en F7, los bytes 1–3 son el ID de fabricante y el byte 4 es la familia. Los mensajes de otros puertos que no sean la entrada seleccionada se ignoran.
- [O] En las respuestas, el byte 7 es un dato de cabecera (en la respuesta de versión es la "versión de comms") y el payload ocupa desde el byte 8 hasta el penúltimo.

### 3.1 Opcodes

| Opcode (dec) | Dirección | Uso observado |
|---|---|---|
| 5 | PC→synth | Pedir todos los ajustes globales |
| 6 | synth→PC | Respuesta con los ajustes globales (ver §4) |
| 10 | PC→synth | Escribir un parámetro: `[10, paramId, valor...]` |
| 11 | PC→synth | Restaurar ajustes por defecto; después la app vuelve a pedir los ajustes |
| 90 | synth→PC | **Cambio desde el panel**: `[paramId, valor]`. El paramId 1 se interpreta como prioridad paráfona si el modo paráfono está activo [O] |
| 114 | synth→PC | **Afinador**: tras cada petición 5, una ráfaga de **50 lecturas en ~2 s** (§6) [O, hardware] |
| 115 | PC→synth | Pedir la versión de software |
| 116 | synth→PC | Respuesta de versión |
| 16, 113, 119 | — | [?] Definidos, pero no se encontró quién los envía ni quién los consume (`unk_b1a03cc1…`). **No se envían nunca** desde Neutron Manager |

- [O] Mientras no hay conexión, solo salen los opcodes 5 y 115. El resto se descarta sin error.
- [O] Una vez concedido el acceso MIDI, se envía el opcode 5 **cada 1000 ms** como sondeo.
- [O] Handshake:
  1. Desconectado → al recibir cualquier mensaje Neutron que no sea el 116, se envía el 115.
  2. Al llegar el 116, se lee la versión. Si cumple el mínimo, se pasa a CONECTADO y se envía el 5; si no, el estado es OUTDATED.

### 3.2 Versión de firmware

- [O] El payload del 116 es texto ASCII. Para leerlo se descarta todo lo que no sea dígito o punto y se separa por `.`. Si salen menos de 3 partes, la segunda parte se reparte en dos dígitos: `"2.02"` → 2.0.2.
- [O] Versión empaquetada = `mayor<<16 | menor<<8 | parche`. Mínimo exigido: **2.0.2**. Las versiones con mayor = 0 se aceptan siempre (versión de desarrollo).
- [O, hardware] Un Neutron real con firmware 2.0.2 responde `F0 00 20 32 28 00 74 01 32 2E 30 2E 32 F7`: el byte 5 (dispositivo) vale `00` en las respuestas, la versión de comms es 1 y la de firmware es el texto `2.0.2`. Captura en `research/probe_read.log`.

## 4. Codificación de 7 bits ("packed")

- [O] **Codificar** (8 bits → 7 bits):
  - Se rellena la entrada con ceros hasta un múltiplo de 7. Si ya es múltiplo, se añaden **7** ceros más.
  - Por cada grupo de 7 bytes se emiten los 7 bytes con el bit 7 quitado, seguidos de un byte con los bits altos: el bit *i* corresponde al byte *i* del grupo.
  - Resultado: 8 bytes por cada 7.
- [O] **Decodificar**: todos los bytes deben ser menores que 0x80 y la longitud múltiplo de 8; si no, es un error. Es la operación inversa.
- [I, confianza alta] El orden de los bits es el estándar de Behringer: el byte de bits altos va **después** de los 7 bytes de datos.

## 5. Ajustes globales (respuesta 6)

- [O] El payload decodificado se interpreta como **10 palabras de 16 bits little-endian** (bloques 0–9). Cada parámetro es un campo de bits `(bloque, desplazamiento, ancho)`; valor = `(palabra[bloque] >> desplazamiento) & ((1<<ancho)-1)`.
- [O] Los booleanos valen `true` solo si el campo es 1. Los parámetros de tipo array reparten el campo en partes iguales; el elemento *k* ocupa los bits `k*ancho_elem`.

| Parámetro | paramId (escritura) | Bloque | Desplazamiento | Ancho | Transformación al leer |
|---|---|---|---|---|---|
| OSC1_RANGE | 38 | 0 | 0 | 2 | |
| OSC2_RANGE | 39 | 0 | 2 | 2 | |
| OSC_SYNC | 14 | 0 | 4 | 1 | |
| LFO_KEY_SYNC | 55 | 0 | 5 | 1 | |
| VCF_KEY_TRACK | 17 | 0 | 6 | 1 | |
| PARAPHONIC_MODE | 15 | 0 | 7 | 1 | |
| VCF_MODE | 16 | 0 | 8 | 2 | |
| ASSIGN_OUT | 4 | 0 | 10 | 3 | |
| OSC1_BLEND_MODE | 32 | 0 | 13 | 1 | |
| OSC2_BLEND_MODE | 33 | 0 | 14 | 1 | |
| LFO_BLEND_MODE | 48 | 0 | 15 | 1 | |
| ENV_RETRIGGER | 5 | 1 | 0 | 1 | |
| OSC1_TUNE_POT | 34 | 1 | 1 | 1 | |
| OSC2_TUNE_POT | 35 | 1 | 2 | 1 | |
| POLYCHAIN_MODE | 8 | 1 | 3 | 2 | |
| DEVICE_ID | 9 | 1 | 5 | 4 | |
| MIDI_CHANNEL | 0 | 1 | 9 | 4 | |
| DISABLE_MIDI_DIPS | 10 | 1 | 13 | 1 | |
| NOTE_PRIORITY | 1 | 1 | 14 | 2 | |
| PITCH_BEND_RANGE | 3 | 2 | 0 | 5 | |
| OSC1_AUTOGLIDE | 36 | 2 | 5 | 5 | |
| OSC2_AUTOGLIDE | 37 | 2 | 10 | 5 | |
| OSC2_KEY_TRACK | 42 | 2 | 15 | 1 | |
| LFO_SHAPE_ORDER | 56 | 3 | 0 | 15 | array (escritura por índice, §5.1) |
| LFO_MIDI_SYNC | 53 | 3 | 15 | 1 | |
| LFO_KEY_TRACK | 50 | 4 | 0 | 7 | |
| OSC_KEY_SPLIT | 40 | 4 | 10 | 6 | si el valor es 0 → 0; si no → valor+23 |
| LFO_PHASE_OFFSET | 58 | 5 | 0 | 15 | array |
| KEY_RANGE_MUTE | 11 | 5 | 15 | 1 | |
| KEY_RANGE_MIN | 12 | 6 | 0 | 6 | valor+24 |
| KEY_RANGE_MAX | 13 | 6 | 6 | 6 | 96−valor |
| VCF_MOD_SRC | 18 | 6 | 12 | 2 | |
| LFO_ONE_SHOT | 49 | 6 | 14 | 1 | |
| LFO_RETRIGGER | 59 | 6 | 15 | 1 | |
| VCF_MOD_DEPTH | 20 | 7 | 0 | 6 | |
| LFO_DEPTH | 52 | 7 | 6 | 6 | |
| PARA_NOTE_PRIORITY | 1 | 7 | 12 | 2 | (mismo paramId que NOTE_PRIORITY) |

- [O] Acciones sin estado: RESET_KEY_RANGE = paramId 6 y RESET_LFO_ORDER = paramId 57. Se envían como `[10, id, 0]` y luego se vuelven a pedir los ajustes.
- [O] Los bloques 8 y 9 se reciben, pero ningún parámetro los usa.
- [O] Significado de los valores (sacado de la interfaz y descrito con mis palabras):
  - RANGE: 0 = 32', 1 = 16', 2 = 8', 3 = ±10 oct (no disponible si el TUNE del osc está ignorado).
  - VCF_MODE: 0 = paso alto, 1 = paso banda, 2 = paso bajo.
  - VCF_MOD_SRC: 0 = apagado, 1 = aftertouch, 2 = rueda de modulación, 3 = velocidad.
  - ASSIGN_OUT: 0 = CV osc 1, 1 = CV osc 2, 2 = velocidad, 3 = rueda, 4 = aftertouch.
  - NOTE_PRIORITY: 0 = grave, 1 = aguda, 2 = última; la versión paráfona añade 3 = aguda/grave.
  - AUTOGLIDE: índice 0–24 = −12…+12 semitonos (12 = 0).
  - PITCH_BEND_RANGE: 0–24 semitonos.
  - MIDI_CHANNEL: 0–15 = canal 1–16.
  - VCF_MOD_DEPTH y LFO_DEPTH: 0–63, mostrados en %.
  - OSC_KEY_SPLIT: 0 = desactivado o nota 24–86.
  - LFO_KEY_TRACK: 0 = desactivado o nota 12–108.
  - KEY_RANGE: mínimo 24–87 y máximo 33–96.
  - LFO_SHAPE_ORDER: 5 posiciones × forma (seno, triángulo, sierra inversa, cuadrada, sierra).
  - LFO_PHASE_OFFSET: 5 formas × fase (0°–315° en pasos de 45°).
  - Booleanos de "blend": encendido = cambio por pasos. OSC2_KEY_TRACK encendido = el osc 2 no sigue el teclado. ENV_RETRIGGER encendido = legato.
- [O, hardware] Los ajustes leídos de un Neutron real se decodifican así: rango 16'/8', paso bajo, canal 1, pitch bend 2, prioridad "última", orden del LFO 0-1-2-3-4, rango de teclas 24–96 y profundidades 63.
- [O] La interfaz escribe el paramId 1 en la prioridad paráfona cuando el modo paráfono está activo, y en la normal cuando no. Al recibir el opcode 90 con paramId 1 aplica la misma regla.

### 5.1 Escritura de parámetros (opcode 10)

- [O] Booleano: `[10, id, 0|1]`, enviando el valor contrario al actual (alternar).
- [O] Entero sin signo: `[10, id, v & 0x7F]`. Los valores no enteros o negativos se rechazan antes de enviar.
- [O] Array: un mensaje por elemento, `[10, id, índice & 0x7F, valor & 0x7F]`.
- [O] La escritura **no** usa la codificación de 7 bits; los valores van en crudo, enmascarados a 7 bits.
- [I, confianza media] Los parámetros que en la respuesta llevan transformación (+23, +24, 96−x) se escriben en el dominio de la UI, es decir, como número de nota. Hay que confirmarlo con hardware antes de escribir KEY_RANGE y OSC_KEY_SPLIT.

## 6. Afinador en vivo (114)

- [O] Payload decodificado de 14 bytes = 2 grupos de 7 bytes. De cada grupo, los 4 primeros forman un entero little-endian: el **periodo medido del oscilador en microsegundos** (frecuencia = 10⁶ / periodo). El grupo 0 es el OSC 1 y el grupo 1 el OSC 2. El byte 4 del grupo 0 es un número de nota MIDI.
- [O, hardware] **No es continuo**: cada petición de ajustes (opcode 5) dispara una ráfaga de 50 lecturas repartidas en ~1,96 s (unas 25/s). La petición de versión (115) no dispara nada, y sin peticiones no llega nada. Por eso el sondeo cada 1000 ms de §3.1 mantiene el afinador vivo. Captura en `research/burst_test.log`.
- Ejemplo capturado: 2446 µs → 408,8 Hz; 5592 µs → 178,8 Hz.

## 6b. Mandos y botones del panel

- [O, hardware] En 30 s de escucha pasiva y en 40 s tras el saludo, moviendo mandos y pulsando botones, el Neutron **no envió ningún mensaje**: ni CC, ni notas, ni opcode 90 (`research/listen_knobs.log`, `research/listen_after_hello.log`).
- [I, confianza alta] Las posiciones de los mandos no se pueden leer por MIDI. Encaja con el hardware: el chip solo lee 5 potenciómetros y no tiene ningún mensaje para enviarlos.
- [I] Los cambios de ajustes hechos desde el panel se detectan comparando cada respuesta del sondeo, no por un aviso del synth. Que el opcode 90 nunca llegara al pulsar botones está pendiente de confirmar.

## 7. Firmware alternativo r15f (mrf-r)

- [O] `r15f_08000000.bin` ocupa 78 436 bytes y se carga en `0x08000000`. Dispositivo DFU: VID `0x0483`, PID `0xDF11` (bootloader ROM de ST), alt 0. Fuente: `README_MAC_LINUX.txt` y el manual.
- [O] El bootloader ROM se activa encendiendo el synth con el botón BOOT pulsado. No se puede borrar.
- [O] r15f guarda su estado en la página flash `0x0801C800`. Fuente: `nvm.c` (GPL).
- [O] r15f no procesa SysEx (el manejador de inicio de SysEx no hace nada) y hace MIDI thru por DIN. Fuente: `midi.c` (GPL).
- [I, confianza media] r15f no implementa USB-MIDI: el binario no tiene descriptores USB legibles (`unk_95e27560…`).
- [O] Toda la configuración de r15f se hace con combinaciones de botones (`manual.txt`). Esas combinaciones forman el contenido de la guía interactiva de la app.
- [?] Dónde guarda la calibración el firmware de fábrica y si un borrado DFU la elimina (`unk_91860783…`, **crítico**). La app debe hacer un backup completo de 128 KB antes de cualquier escritura.

## 8. DFU

El protocolo DFU 1.1 con extensiones DfuSe es una especificación pública de ST (AN3156) y del USB-IF. No sale de ningún objetivo analizado y se implementa directamente a partir de esas especificaciones.
