import FreeCAD
import FreeCADGui
import hashlib
import json
import time
from collections import defaultdict

# Vereinfachter PySide-Import
try:
    from PySide6 import QtCore, QtWidgets, QtGui
    QAction = QtGui.QAction
except ImportError:
    from PySide2 import QtCore, QtWidgets, QtGui
    QAction = QtWidgets.QAction

# Globale Variablen und Konfiguration
# ===================================

# Optimierte Konfiguration
config = {
    "history_length": 5,              # Anzahl der zu speichernden Historie-Einträge
    "recompute_delay": 300,           # Verzögerung für Recompute-Timer in ms
    "dependency_depth": 3,            # Tiefe der Abhängigkeitsverfolgung
    "batch_size": 50,                 # Anzahl der Objekte pro Batch
    "edit_check_interval": 200,       # Prüfintervall für Edit-Mode in ms (beschleunigt)
    "throttle_interval": 100          # Minimale Zeit zwischen Änderungsverarbeitung in ms
}

# Globale Statusvariablen
observer_active = True
recompute_queue = set()
recompute_timer = None
changed_properties = defaultdict(list)
feature_cache = {}
dependency_cache = {}
pending_output = False

# Zustandsverfolgung für Task Panels
task_panel_active = False
changes_during_task = False
edit_mode_active = False
last_change_time = 0  # Für Throttling von Änderungen

# Wichtige Eigenschaften pro Objekttyp für topologische Identifikation
important_props = {
    # Grundlegende Part-Features
    "Part::Feature": ["Placement", "Length", "Width", "Height"],
    "Part::Box": ["Length", "Width", "Height", "Placement"],
    "Part::Cylinder": ["Radius", "Height", "Placement", "Angle"],
    
    # Sketcher
    "Sketcher::SketchObject": ["Geometry", "Constraints", "ExternalGeometry"],
    
    # PartDesign Features
    "PartDesign::Pad": ["Length", "Direction", "Type", "Reversed", "Midplane", "Offset", "Shape"],
    "PartDesign::Pocket": ["Length", "Direction", "Type", "Reversed", "Midplane", "Offset", "Shape"],
    "PartDesign::Revolution": ["Angle", "Axis", "Base", "Midplane", "Reversed", "Shape"],
    "PartDesign::AdditiveLoft": ["Sections", "Ruled", "Closed", "Shape"],
    "PartDesign::SubtractiveLoft": ["Sections", "Ruled", "Closed", "Shape"],
    "PartDesign::Fillet": ["Radius", "Base", "FilletType", "Shape"],
    "PartDesign::Chamfer": ["Size", "Angle", "Base", "Shape"],
    "PartDesign::Mirrored": ["MirrorPlane", "Originals", "Shape"],
    "PartDesign::LinearPattern": ["Direction", "Occurrences", "Length", "Reversed", "Shape"],
    "PartDesign::PolarPattern": ["Axis", "Occurrences", "Angle", "Reversed", "Shape"],
    "PartDesign::Groove": ["Angle", "Axis", "Base", "Reversed", "Midplane", "Shape"],
    
    # Part Features
    "Part::Thickness": ["Value", "Mode", "Join", "Shape"],
    "Part::Helix": ["Pitch", "Height", "Radius", "Angle", "Growth", "Shape"],
    
    # Weitere wichtige Features
    "Part::Cut": ["Base", "Tool", "Shape"],
    "Part::Fuse": ["Base", "Tool", "Shape"],
    "Part::Common": ["Base", "Tool", "Shape"],
    "Part::MultiCut": ["Shapes", "Shape"],
    "Part::Compound": ["Links", "Shape"],
    "Part::Offset": ["Value", "Mode", "Join", "Shape"],
    "Part::Offset2D": ["Value", "Join", "Fill", "Shape"],
    "Part::Loft": ["Sections", "Solid", "Ruled", "Closed", "Shape"],
    "Part::Sweep": ["Spine", "Profiles", "Solid", "Frenet", "Shape"]
}

# Allgemeine Eigenschaften für alle Objekttypen (ohne Visibility)
general_props = ["Label", "Placement", "Shape"]

# Kernfunktionen
# =============

def get_feature_data(obj):
    """Sammelt wichtige Daten eines Features - OPTIMIERT"""
    data = {
        "Type": obj.TypeId,
        "Label": obj.Label,
        "Parameters": {}
    }
    
    # Optimiert: Wähle relevante Eigenschaften nur einmal aus
    type_props = important_props.get(obj.TypeId, []) + general_props
    relevant_props = set(type_props) & set(obj.PropertiesList)
    
    # Parameter extrahieren - nur wichtige Eigenschaften
    for prop in relevant_props:
        try:
            if prop in ["FeatureHash", "FeatureHistory"]:
                continue
                
            value = getattr(obj, prop)
            
            # Optimierte Verarbeitung je nach Eigenschaftstyp
            if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
                data["Parameters"][prop] = (value.x, value.y, value.z)
            elif isinstance(value, FreeCAD.Placement):
                # Vereinfachte Platzierungsdaten
                data["Parameters"][prop] = {
                    "Base": (value.Base.x, value.Base.y, value.Base.z),
                    "Rotation": str(value.Rotation)
                }
            elif prop == "Shape" and hasattr(value, "CenterOfMass"):
                # Für Shapes nur die wichtigsten Eigenschaften speichern
                data["Parameters"][prop] = {
                    "CenterOfMass": (value.CenterOfMass.x, value.CenterOfMass.y, value.CenterOfMass.z),
                    "BoundBox": (value.BoundBox.DiagonalLength, value.BoundBox.XLength, 
                                value.BoundBox.YLength, value.BoundBox.ZLength),
                    "Volume": getattr(value, "Volume", 0)
                }
            else:
                data["Parameters"][prop] = str(value)
        except:
            pass
            
    return data

def calculate_hash(data):
    """Berechnet einen Hash der Feature-Daten - OPTIMIERT"""
    # Schnellere Serialisierung durch Beschränkung auf relevante Daten
    reduced_data = {
        "Type": data["Type"],
        "Parameters": data["Parameters"]
    }
    json_str = json.dumps(reduced_data, sort_keys=True)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()

def process_feature(obj):
    """Verarbeitet ein einzelnes Feature"""
    try:
        obj_id = obj.Name
        
        # Cache-Überprüfung
        if obj_id in feature_cache:
            cache_time, cache_hash = feature_cache[obj_id]
            last_modified = getattr(obj, "TimeStamp", 0)
            if last_modified <= cache_time:
                return False
        
        # Hash berechnen
        feature_data = get_feature_data(obj)
        feature_hash = calculate_hash(feature_data)
        
        # Properties erstellen falls nötig
        if not hasattr(obj, "FeatureHistory"):
            obj.addProperty("App::PropertyStringList", "FeatureHistory", "Meta", "Feature change history")
        
        if not hasattr(obj, "FeatureHash"):
            obj.addProperty("App::PropertyString", "FeatureHash", "Meta", "Feature hash")
        
        # Nur aktualisieren wenn nötig
        if not hasattr(obj, "FeatureHash") or obj.FeatureHash != feature_hash:
            # History aktualisieren
            history = getattr(obj, "FeatureHistory", [])
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            history_entry = f"{timestamp}: {feature_hash}"
            
            if history and history[-1].endswith(feature_hash):
                history[-1] = history_entry
            else:
                history.append(history_entry)
                obj.FeatureHistory = history[-config["history_length"]:]
            
            # Hash aktualisieren
            obj.FeatureHash = feature_hash
            feature_cache[obj_id] = (time.time(), feature_hash)
            
            # Cache zurücksetzen
            if obj_id in dependency_cache:
                del dependency_cache[obj_id]
                
            return True
        else:
            feature_cache[obj_id] = (time.time(), feature_hash)
            
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"Fehler bei {obj.Name}: {str(e)}\n")
    
    return False

def sort_by_dependencies(objects):
    """Sortiert Objekte nach Abhängigkeiten (Basis-Objekte zuerst)"""
    # Abhängigkeitsdiagramm erstellen
    graph = {}
    for obj in objects:
        graph[obj.Name] = [child.Name for child in obj.OutList if child in objects]
    
    # Topologische Sortierung
    visited = set()
    result = []
    
    def visit(node_name):
        if node_name in visited:
            return
        visited.add(node_name)
        for child in graph.get(node_name, []):
            visit(child)
        for obj in objects:
            if obj.Name == node_name:
                result.append(obj)
                break
    
    for obj in objects:
        visit(obj.Name)
    
    return result

def process_with_depth_limit(obj, depth=0, processed=None):
    """Verarbeitet Objekte mit begrenzter Rekursionstiefe"""
    if processed is None:
        processed = set()
    
    if obj.Name in processed:
        return
    
    processed.add(obj.Name)
    process_feature(obj)
    
    # Rekursionsabbruch bei Tiefe 1
    if depth == 1:
        return
    
    # Rekursiv Kinder verarbeiten
    next_depth = depth - 1 if depth > 0 else 0
    for child in obj.OutList:
        process_with_depth_limit(child, next_depth, processed)

def process_affected_features(obj):
    """Verarbeitet geändertes Objekt und seine Abhängigkeiten"""
    if not FreeCAD.ActiveDocument:
        return
    
    process_with_depth_limit(obj, config["dependency_depth"])

def process_all_features():
    """Verarbeitet alle Features im Dokument"""
    doc = FreeCAD.ActiveDocument
    if not doc:
        FreeCAD.Console.PrintError("Kein aktives Dokument!\n")
        return
    
    FreeCAD.Console.PrintMessage("Starte vollständige Analyse aller Features...\n")
    start_time = time.time()
    processed = 0
    
    # Objekte nach Abhängigkeiten sortieren
    sorted_objects = sort_by_dependencies(doc.Objects)
    batch_size = config["batch_size"]
    total_objects = len(sorted_objects)
    
    for i in range(0, total_objects, batch_size):
        batch = sorted_objects[i:i+batch_size]
        batch_processed = 0
        
        for obj in batch:
            if process_feature(obj):
                processed += 1
                batch_processed += 1
        
        # Fortschritt anzeigen
        FreeCAD.Console.PrintMessage(f"Verarbeite {i+batch_processed}/{total_objects} Objekte...\r")
        QtCore.QCoreApplication.processEvents()
    
    elapsed = time.time() - start_time
    FreeCAD.Console.PrintMessage(f"\nFertig! {processed} Objekte verarbeitet in {elapsed:.2f}s\n")

# Observer-Funktionen
# ==================

class DocumentObserver:
    """Observer-Klasse für Dokument-Änderungen"""
    def __init__(self):
        self.active = True
        # Erweiterte Liste ignorierter Eigenschaften
        self.ignored_props = [
            "FeatureHash", "FeatureHistory", "_GroupTouched", 
            "Visibility", "FullyConstrained",  # Diese Eigenschaften sind für Toponaming nicht relevant
            "EditModes", "EditMode", "EditCurves", "EditHighlight", "HiddenLines" # Sketcher-interne Eigenschaften
        ]
        # Für Throttling
        self.last_change_time = time.time()
        self.throttle_queue = {}  # Objekt -> Eigenschaften für verzögerte Verarbeitung
        self.throttle_timer = None
    
    def slotCreatedObject(self, obj):
        """Wird aufgerufen, wenn ein neues Objekt erstellt wird"""
        global recompute_queue, changed_properties, pending_output, changes_during_task
        if not (self.active and observer_active):
            return
            
        # Änderung registrieren mit Throttling
        if time.time() - self.last_change_time < config["throttle_interval"] / 1000.0:
            # Zu schnelle Änderung, verzögern
            self.queue_change(obj, ["Neues Objekt"])
            return
            
        self.last_change_time = time.time()
        
        # Ist Edit-Modus oder Task-Panel aktiv?
        if edit_mode_active or task_panel_active:
            # Nur Änderungen sammeln
            recompute_queue.add(obj)
            changed_properties[obj.Label] = ["Neues Objekt"]
            changes_during_task = True
            return
        
        # Normaler Modus - direkt verarbeiten
        recompute_queue.add(obj)
        changed_properties[obj.Label] = ["Neues Objekt"]
        
        if not pending_output:
            pending_output = True
            FreeCAD.Console.PrintMessage("Änderung an :\n")
        start_recompute_timer()

    def slotChangedObject(self, obj, prop):
        """Wird aufgerufen, wenn ein Objekt geändert wird"""
        global recompute_queue, changed_properties, pending_output, changes_during_task
        if not (self.active and observer_active) or prop in self.ignored_props:
            return
            
        # Änderung registrieren mit Throttling
        if time.time() - self.last_change_time < config["throttle_interval"] / 1000.0:
            # Zu schnelle Änderung, verzögern
            self.queue_change(obj, [prop])
            return
            
        self.last_change_time = time.time()
        
        # Ist Edit-Modus oder Task-Panel aktiv?
        if edit_mode_active or task_panel_active:
            # Nur Änderungen sammeln
            recompute_queue.add(obj)
            if prop not in changed_properties[obj.Label]:
                changed_properties[obj.Label].append(prop)
            changes_during_task = True
            return
        
        # Normaler Modus - direkt verarbeiten
        recompute_queue.add(obj)
        obj_label = obj.Label
        if prop not in changed_properties[obj_label]:
            changed_properties[obj_label].append(prop)
        
        if not pending_output:
            pending_output = True
            FreeCAD.Console.PrintMessage("Änderung an :\n")
        start_recompute_timer()
    
    def queue_change(self, obj, props):
        """Sammelt Änderungen für verzögertes Throttling"""
        if obj.Name not in self.throttle_queue:
            self.throttle_queue[obj.Name] = {"obj": obj, "props": set()}
        
        for prop in props:
            self.throttle_queue[obj.Name]["props"].add(prop)
            
        # Timer für verzögerte Verarbeitung starten/zurücksetzen
        if self.throttle_timer:
            self.throttle_timer.stop()
        
        self.throttle_timer = QtCore.QTimer()
        self.throttle_timer.timeout.connect(self.process_throttled_changes)
        self.throttle_timer.setSingleShot(True)
        self.throttle_timer.start(config["throttle_interval"])
    
    def process_throttled_changes(self):
        """Verarbeitet gesammelte Änderungen nach Throttling-Intervall"""
        global recompute_queue, changed_properties, pending_output, changes_during_task
        
        # Änderungen aus Queue verarbeiten
        for obj_data in self.throttle_queue.values():
            obj = obj_data["obj"]
            props = obj_data["props"]
            
            # Ist Edit-Modus oder Task-Panel aktiv?
            if edit_mode_active or task_panel_active:
                # Nur Änderungen sammeln
                recompute_queue.add(obj)
                for prop in props:
                    if prop not in changed_properties[obj.Label]:
                        changed_properties[obj.Label].append(prop)
                changes_during_task = True
            else:
                # Normaler Modus - direkt verarbeiten
                recompute_queue.add(obj)
                for prop in props:
                    if prop not in changed_properties[obj.Label]:
                        changed_properties[obj.Label].append(prop)
                
                if not pending_output:
                    pending_output = True
                    FreeCAD.Console.PrintMessage("Änderung an :\n")
                start_recompute_timer()
        
        # Queue leeren
        self.throttle_queue = {}
        self.last_change_time = time.time()

# Optimierte EditModeObserver-Klasse
class EditModeObserver:
    """Beobachtet den Bearbeitungsmodus in FreeCAD"""
    def __init__(self):
        self.active = True
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.check_edit_mode)
        self.timer.start(config["edit_check_interval"])  # Schnellere Überprüfung
        self.in_edit_last = False  # Cache für den letzten Status
    
    def check_edit_mode(self):
        """Prüft, ob ein Objekt im Bearbeitungsmodus ist - OPTIMIERT"""
        global edit_mode_active, changes_during_task
        
        if not self.active:
            return
            
        try:
            # Schnellere Prüfung mit Cache
            in_edit = False
            
            # Schnellere, direkte Prüfung des aktiven Dokuments
            if FreeCADGui.ActiveDocument:
                active_edit = FreeCADGui.ActiveDocument.getInEdit()
                in_edit = active_edit is not None
                
                # Zusätzliche Prüfung für Sketcher nur wenn nötig
                if not in_edit:
                    active_wb = FreeCADGui.activeWorkbench()
                    if active_wb and "Sketcher" in active_wb.__class__.__name__:
                        # Prüfe, ob ein Sketcher-Task aktiv ist
                        mw = FreeCADGui.getMainWindow()
                        if mw:
                            taskpanel = mw.findChild(QtWidgets.QWidget, "TaskPanel")
                            if taskpanel:
                                for child in taskpanel.children():
                                    if child and hasattr(child, "objectName") and "sketch" in str(child.objectName()).lower():
                                        in_edit = True
                                        break
            
            # Status-Update nur bei Änderung (reduziert Ausgaben)
            if in_edit != self.in_edit_last:
                self.in_edit_last = in_edit
                if in_edit:
                    FreeCAD.Console.PrintMessage("Bearbeitungsmodus aktiviert - Änderungen werden gepuffert\n")
                    edit_mode_active = True
                    changes_during_task = False
                else:
                    FreeCAD.Console.PrintMessage("Bearbeitungsmodus beendet\n")
                    edit_mode_active = False
                    
                    # Kurze Verzögerung, dann prüfen ob Änderungen verarbeitet werden sollen
                    QtCore.QTimer.singleShot(100, check_edit_completion)
        except:
            # Fehler ignorieren
            pass

# Task Panel Observer
class TaskPanelObserver(QtCore.QObject):
    """Observer für Aufgabenbereiche (Task Panels)"""
    def __init__(self):
        super().__init__()
        self.active = True
        self.detected_panels = set()  # Schnellere Prüfung auf bereits erkannte Panels
        
    def eventFilter(self, obj, event):
        """Überwacht Events des Hauptfensters - OPTIMIERT"""
        global task_panel_active, changes_during_task
        
        if not self.active:
            return False
            
        # Optimierte Erkennung von Task Panels
        if event.type() == QtCore.QEvent.ChildAdded:
            child = event.child()
            if isinstance(child, QtWidgets.QWidget):
                obj_name = child.objectName()
                # Schnellere String-Prüfung
                is_task_panel = (obj_name == "TaskPanel" or 
                                "task" in obj_name.lower() or
                                "panel" in obj_name.lower())
                
                if is_task_panel and obj_name not in self.detected_panels:
                    self.detected_panels.add(obj_name)
                    FreeCAD.Console.PrintMessage(f"Task Panel geöffnet ({obj_name}) - Änderungen werden gepuffert\n")
                    task_panel_active = True
                    changes_during_task = False
                
        # Erkennung von geschlossenen Task Panels
        elif event.type() == QtCore.QEvent.ChildRemoved:
            child = event.child()
            if isinstance(child, QtWidgets.QWidget):
                obj_name = child.objectName()
                if obj_name in self.detected_panels:
                    self.detected_panels.remove(obj_name)
                    FreeCAD.Console.PrintMessage(f"Task Panel geschlossen ({obj_name})\n")
                    
                    # Prüfen, ob noch andere Panels aktiv sind
                    if not self.detected_panels:
                        task_panel_active = False
                        # Kurze Verzögerung, um FreeCAD Zeit zu geben, Änderungen zu verarbeiten
                        QtCore.QTimer.singleShot(100, check_task_completion)
                
        return False  # Event weiterleiten

# Globale Observer-Instanzen
document_observer = None
task_panel_observer = None
edit_mode_observer = None

def check_edit_completion():
    """Prüft, ob nach Beenden des Bearbeitungsmodus Änderungen verarbeitet werden sollen"""
    global changes_during_task, pending_output
    
    # Nur verarbeiten wenn wir nicht in einem Task Panel sind
    if task_panel_active:
        return
        
    # Wenn Änderungen während der Bearbeitung vorgenommen wurden, verarbeiten
    if changes_during_task and recompute_queue:
        FreeCAD.Console.PrintMessage("Bearbeitungsmodus mit Änderungen beendet - verarbeite Änderungen\n")
        pending_output = True
        FreeCAD.Console.PrintMessage("Änderung an :\n")
        print_changes()
        start_recompute_timer()
    else:
        FreeCAD.Console.PrintMessage("Bearbeitungsmodus ohne Änderungen beendet\n")
        # Änderungen verwerfen
        recompute_queue.clear()
        changed_properties.clear()

def check_task_completion():
    """Prüft, ob nach dem Schließen eines Task Panels Änderungen verarbeitet werden sollen"""
    global changes_during_task, pending_output
    
    # Nicht verarbeiten, wenn wir noch im Edit-Modus sind
    if edit_mode_active:
        return
        
    # Wenn Änderungen während des Task Panels vorgenommen wurden, verarbeiten
    if changes_during_task and recompute_queue:
        FreeCAD.Console.PrintMessage("Task Panel mit Änderungen abgeschlossen - verarbeite Änderungen\n")
        pending_output = True
        FreeCAD.Console.PrintMessage("Änderung an :\n")
        print_changes()
        start_recompute_timer()
    else:
        FreeCAD.Console.PrintMessage("Task Panel ohne Änderungen geschlossen\n")
        # Änderungen verwerfen
        recompute_queue.clear()
        changed_properties.clear()

def print_changes():
    """Gibt gesammelte Änderungen aus"""
    global changed_properties
    
    # Sortierte Ausgabe aller geänderten Objekte
    for obj_label in sorted(changed_properties.keys()):
        props_list = changed_properties[obj_label]
        props_str = ", ".join(props_list)
        FreeCAD.Console.PrintMessage(f"-->{obj_label}: {props_str}\n")

def on_recompute_timer():
    """Verarbeitet Änderungen nach Verzögerung"""
    global recompute_queue, changed_properties, pending_output
    if not recompute_queue:
        return
    
    # Nicht verarbeiten, wenn wir im Edit-Modus oder Task Panel sind
    if edit_mode_active or task_panel_active:
        return
        
    # Gesammelte Änderungen ausgeben falls nötig
    if pending_output:
        print_changes()
    
    FreeCAD.Console.PrintMessage("Verarbeite geänderte Objekte...\n")
    
    # Objekte nach Abhängigkeiten sortieren
    sorted_objects = sort_by_dependencies(list(recompute_queue))
    
    # In Batches verarbeiten
    batch_size = config["batch_size"]
    for i in range(0, len(sorted_objects), batch_size):
        batch = sorted_objects[i:i+batch_size]
        for obj in batch:
            process_affected_features(obj)
        QtCore.QCoreApplication.processEvents()
    
    recompute_queue.clear()
    changed_properties.clear()
    pending_output = False
    
    FreeCAD.Console.PrintMessage("Aktualisierung abgeschlossen!\n")

def start_recompute_timer():
    """Startet oder setzt den Verarbeitungstimer zurück"""
    global recompute_timer
    
    # Nur starten, wenn kein Edit-Modus oder Task Panel aktiv ist
    if edit_mode_active or task_panel_active:
        return
        
    if recompute_timer:
        recompute_timer.stop()
    
    recompute_timer = QtCore.QTimer()
    recompute_timer.timeout.connect(on_recompute_timer)
    recompute_timer.setSingleShot(True)
    recompute_timer.start(config["recompute_delay"])

# Steuerungsfunktionen
# ==================

def setup_observers():
    """Richtet die Observer für Änderungen ein"""
    global observer_active, document_observer, task_panel_observer, edit_mode_observer
    
    if not document_observer:
        document_observer = DocumentObserver()
    
    if not task_panel_observer:
        task_panel_observer = TaskPanelObserver()
        # EventFilter für das Hauptfenster einrichten
        try:
            main_window = FreeCADGui.getMainWindow()
            if main_window:
                main_window.installEventFilter(task_panel_observer)
                FreeCAD.Console.PrintMessage("Task Panel Observer aktiviert\n")
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Fehler beim Einrichten des Task Panel Observers: {e}\n")
    
    if not edit_mode_observer:
        edit_mode_observer = EditModeObserver()
        FreeCAD.Console.PrintMessage("Edit Mode Observer aktiviert\n")
    
    try:
        FreeCAD.addDocumentObserver(document_observer)
        observer_active = True
        FreeCAD.Console.PrintMessage("Observer aktiviert - Änderungen werden automatisch verfolgt\n")
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"Fehler beim Aktivieren der Observer: {e}\n")

def remove_observers():
    """Entfernt alle Observer"""
    global observer_active, document_observer, task_panel_observer, edit_mode_observer
    observer_active = False
    
    try:
        if document_observer:
            document_observer.active = False
            FreeCAD.removeDocumentObserver(document_observer)
        
        if task_panel_observer:
            task_panel_observer.active = False
            try:
                main_window = FreeCADGui.getMainWindow()
                if main_window:
                    main_window.removeEventFilter(task_panel_observer)
            except:
                pass
        
        if edit_mode_observer:
            edit_mode_observer.active = False
            if edit_mode_observer.timer.isActive():
                edit_mode_observer.timer.stop()
                
        FreeCAD.Console.PrintMessage("Observer deaktiviert\n")
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"Fehler beim Deaktivieren der Observer: {e}\n")

def create_toolbar():
    """Erstellt eine Toolbar für Toponaming"""
    main_window = FreeCADGui.getMainWindow()
    if not main_window:
        return
        
    toolbar = main_window.findChild(QtWidgets.QToolBar, "Toponaming")
    if not toolbar:
        toolbar = main_window.addToolBar("Toponaming")
        toolbar.setObjectName("Toponaming")
    
    toolbar.clear()
    
    # Ein/Aus-Schalter
    toggle_action = QAction("Ein/Aus", main_window)
    toggle_action.setCheckable(True)
    toggle_action.setChecked(observer_active)
    toggle_action.toggled.connect(toggle_toponaming)
    toolbar.addAction(toggle_action)
    
    # Manuelle Analyse-Aktion
    analyze_action = QAction("Analyse starten", main_window)
    analyze_action.triggered.connect(process_all_features)
    toolbar.addAction(analyze_action)

def toggle_toponaming(checked):
    """Schaltet Toponaming ein oder aus"""
    if checked:
        setup_observers()
    else:
        remove_observers()

def start_tracking():
    """Startet das Toponaming-Tracking-System"""
    # Toolbar erstellen
    create_toolbar()
    
    # Observer direkt aktivieren
    setup_observers()
    
    FreeCAD.Console.PrintMessage("\n=== Toponaming-Tracking initialisiert ===\n")
    FreeCAD.Console.PrintMessage("Aktualisierung erfolgt nur nach Abschluss von Aufgabenbereichen\n")

# Tracking starten
start_tracking()