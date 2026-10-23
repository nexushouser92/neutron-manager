"""Panel combinations of the alternative firmware r15f by mrf-r (GPL-3), summarised from its manual.

r15f ignores SysEx and has no USB-MIDI, so it is configured only from the panel; this is the reference
the window shows. Each entry: (group, combination, what it does) in Spanish and English.
"""

GUIDE = {
    "es": [
        ("Guardar y emergencias", [
            ("PARAPHONIC + OSC SYNC", "Guarda todo el estado (calibración, octavas, modos) en la memoria. Parpadean todos los LEDs."),
            ("PARAPHONIC + OSC SYNC + KEY TRK", "Vuelve todos los parámetros a sus valores por defecto."),
            ("PARAPHONIC + FLT KEYTRACK", "Pánico: corta todas las notas."),
        ]),
        ("Osciladores", [
            ("Mantener RANGE de un osc + FLT KEYTRACK", "Autoafinado de ese oscilador."),
            ("Mantener ambos RANGE + FLT KEYTRACK", "Autoafinado de los dos osciladores."),
            ("Mantener RANGE + RANGE del otro osc", "Cambia el modo de rango amplio del mando."),
            ("Mantener PARAPHONIC + RANGE", "Activa o desactiva la modulación por CV y por mando de ese osc."),
            ("Mantener RANGE + girar PORTAMENTO", "Portamento solo para ese oscilador."),
            ("Mantener RANGE + PARAPHONIC", "Modo de edición del osc (4 páginas, se cambia con OSC SYNC): rango de pitch bend, escala del DAC de octava, escala del DAC de notas y afinación absoluta A440. Sale con PARAPHONIC; FLT KEYTRACK autoafina y sale."),
        ]),
        ("Filtro", [
            ("Mantener FILTER MODE + PARAPHONIC", "Ajuste fino de la escala del seguimiento de teclado del filtro."),
        ]),
        ("Menú OSC SYNC (mantener OSC SYNC y pulsar…)", [
            ("LFO SYNC", "Cambia el modo de reinicio de la envolvente."),
            ("OSC RANGE", "Fuente de la salida Assign: osc1, osc2, velocidad, rueda de modulación o aftertouch (0 a 5 V)."),
            ("FLT KEYTRACK", "Edición de la salida Assign: escala y offset."),
        ]),
        ("LFO (mantener LFO SYNC y…)", [
            ("pulsar OSC RANGE", "Cambia de modo de LFO (ver abajo)."),
            ("pulsar OSC SYNC", "Sincronía con el reloj MIDI."),
            ("pulsar FILTER MODE", "Flanco de la entrada de sync: subida, bajada o ambos."),
            ("girar LFO RATE", "Desplaza la fase de 0 a 360°."),
            ("girar LFO SHAPE", "Fase de reinicio."),
        ]),
        ("Modos de LFO", [
            ("1 · Clásico", "El LFO original del Neutron."),
            ("2 · Assign2 SHAPE", "Una segunda salida assign (osc1, breath CC, velocidad, rueda, aftertouch); RATE atenúa."),
            ("3 · Oscilador mono", "Un tercer oscilador a la altura del OSC2. KEY SYNC cambia el rango de RATE."),
            ("4 · Polifónico 5 voces", "5 voces de 2 osciladores con envolvente A-R. Con LFO SYNC encendido: SHAPE = PWM/desafinación, RATE = ataque y release; apagado: SHAPE = nivel del osc2, RATE = release."),
            ("5 · Atenuverter", "Multiplica RATE por SHAPE. KEY SYNC elige saturación o desbordamiento."),
        ]),
        ("Volver al firmware de fábrica", [
            ("Pestaña Firmware → Restaurar copia", "Escribe la copia completa (con tu calibración) tomada antes de instalar r15f."),
            ("Recalibrar mandos (firmware 1.x)", "Encender con OSC SYNC + PARAPHONIC + KEY TRK; en 2.x empieza solo."),
        ]),
    ],
    "en": [
        ("Save and emergencies", [
            ("PARAPHONIC + OSC SYNC", "Saves the whole state (calibration, octaves, modes) to memory. All LEDs blink."),
            ("PARAPHONIC + OSC SYNC + KEY TRK", "Resets every parameter to its default."),
            ("PARAPHONIC + FLT KEYTRACK", "Panic: stops all notes."),
        ]),
        ("Oscillators", [
            ("Hold one osc RANGE + FLT KEYTRACK", "Auto-tunes that oscillator."),
            ("Hold both RANGE + FLT KEYTRACK", "Auto-tunes both oscillators."),
            ("Hold RANGE + other osc RANGE", "Switches the knob's wide-range mode."),
            ("Hold PARAPHONIC + RANGE", "Turns CV and knob modulation of that osc on or off."),
            ("Hold RANGE + turn PORTAMENTO", "Portamento for that oscillator only."),
            ("Hold RANGE + PARAPHONIC", "Osc edit mode (4 pages, cycled with OSC SYNC): pitch bend range, octave DAC scale, note DAC scale and A440 offset. Exit with PARAPHONIC; FLT KEYTRACK auto-tunes and exits."),
        ]),
        ("Filter", [
            ("Hold FILTER MODE + PARAPHONIC", "Fine-tunes the filter key tracking scale."),
        ]),
        ("OSC SYNC menu (hold OSC SYNC and press…)", [
            ("LFO SYNC", "Changes the envelope restart mode."),
            ("OSC RANGE", "Assign output source: osc1, osc2, velocity, mod wheel or aftertouch (0 to 5 V)."),
            ("FLT KEYTRACK", "Assign output edit: scale and offset."),
        ]),
        ("LFO (hold LFO SYNC and…)", [
            ("press OSC RANGE", "Changes the LFO mode (see below)."),
            ("press OSC SYNC", "MIDI clock sync."),
            ("press FILTER MODE", "Sync input edge: rising, falling or both."),
            ("turn LFO RATE", "Shifts the phase from 0 to 360°."),
            ("turn LFO SHAPE", "Retrigger phase."),
        ]),
        ("LFO modes", [
            ("1 · Classic", "The Neutron's original LFO."),
            ("2 · Assign2 SHAPE", "A second assign output (osc1, breath CC, velocity, wheel, aftertouch); RATE attenuates."),
            ("3 · Mono oscillator", "A third oscillator at OSC2's pitch. KEY SYNC changes the RATE range."),
            ("4 · 5-voice poly", "5 voices of 2 oscillators with an A-R envelope. LFO SYNC on: SHAPE = PWM/detune, RATE = attack and release; off: SHAPE = osc2 level, RATE = release."),
            ("5 · Attenuverter", "Multiplies RATE by SHAPE. KEY SYNC picks saturation or wrap-around."),
        ]),
        ("Back to factory firmware", [
            ("Firmware tab → Restore backup", "Writes the full backup (with your calibration) taken before installing r15f."),
            ("Recalibrate knobs (firmware 1.x)", "Power on holding OSC SYNC + PARAPHONIC + KEY TRK; on 2.x it starts by itself."),
        ]),
    ],
}
