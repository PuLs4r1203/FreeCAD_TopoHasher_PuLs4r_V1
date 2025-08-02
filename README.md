# FreeCAD_TopoHasher_PuLs4r_V1

Ein intelligentes Tool für FreeCAD zur Verfolgung und Speicherung topologischer Beziehungen von CAD-Objekten. Der Toponaming-Tracker protokolliert Änderungen in deinen Modellen und erstellt eindeutige Hashes für jedes Feature, was die Stabilität der topologischen Referenzen verbessert.

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![FreeCAD](https://img.shields.io/badge/FreeCAD-0.20+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## Funktionsweise

Der Toponaming-Tracker beobachtet Änderungen im FreeCAD-Dokument und analysiert wichtige Eigenschaften von Features (wie Skizzen, Pads, Pockets, Fillets usw.). Dabei werden:

- Änderungen erst verarbeitet, wenn eine Bearbeitung (z.B. Skizze) abgeschlossen ist
- Eindeutige Hashes für jedes Feature erstellt und in Eigenschaften gespeichert
- Abhängigkeiten zwischen Objekten berücksichtigt
- Verarbeitungsoperationen optimiert, um die Benutzeroberfläche nicht zu blockieren

Das Tool ist besonders hilfreich, um topologische Benennungsprobleme zu reduzieren, die bei komplexen Konstruktionen in FreeCAD auftreten können.

## Installation

1. **Herunterladen**: Lade die Datei `toponaming_tracker.py` herunter.

2. **Installation in FreeCAD**:
   - Option 1 - Makro:
     - Kopiere die Datei in deinen FreeCAD-Makro-Ordner (Extras > Makros > Makros...)
     - Starte das Makro über das Makro-Menü
   
   - Option 2 - Automatischer Start:
     - Kopiere die Datei in den Ordner `~/.FreeCAD/Mod/` (Linux/Mac) oder `%APPDATA%\FreeCAD\Mod\` (Windows)
     - Benenne den Ordner `ToponameTracker`
     - Erstelle eine Datei `Init.py` mit folgendem Inhalt:
       ```python
       # FreeCAD ToponameTracker InitScript
       import toponaming_tracker
       ```

3. **Erstmaliger Start**: Beim ersten Start wird automatisch eine Toolbar mit zwei Schaltflächen erstellt:
   - Ein/Aus: Aktiviert oder deaktiviert das Toponaming-Tracking
   - Analyse starten: Führt eine vollständige Analyse aller Objekte im Dokument durch

## Verwendung

Der Toponaming-Tracker arbeitet hauptsächlich im Hintergrund:

1. **Tracking-Modi**:
   - Der Tracker sammelt Änderungen während der Bearbeitung von Features
   - Erst wenn ein Task Panel geschlossen oder der Bearbeitungsmodus beendet wird, werden die Änderungen verarbeitet
   - Dies verhindert unnötige Verarbeitungsoperationen während der Modellerstellung

2. **Tracking-Daten**: Für jedes Objekt werden zwei neue Eigenschaften erstellt:
   - `FeatureHash`: Ein eindeutiger Hash-Wert, der das Feature charakterisiert
   - `FeatureHistory`: Eine Liste der letzten Änderungen mit Zeitstempeln

3. **Ausgabe**: In der FreeCAD-Konsole werden Änderungen protokolliert:
