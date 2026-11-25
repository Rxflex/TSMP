"""A guest editing its sim in Create-a-Sim, and the host taking the new look.

Create-a-Sim is the game client's own: it edits a sim of the simulation it
belongs to and, when it closes, has the zone loaded again from the scratch
save. A joined guest's simulation is not the one that counts, so its edits
would be lost when it loads the host's state again. So the guest notes how
its sim looked before (`take`), and once its zone is back sends what changed
to the host, which puts it on its own copy of the sim (`apply`) before the
guest asks for its state.

What is sent is the part of the sim's SimData that Create-a-Sim edits: names,
voice, skin, physique, face, genetics, outfits. Age, gender, species, traits
and occult forms are left as they are on the host: changing them on a sim
that stands on the lot is not done by these calls.

`copy` is pure; the rest only runs inside the game.
"""

from tsmp import log

# Fields of SimData that hold how a sim looks and sounds.
FIELDS = (
    "first_name", "last_name", "first_name_key", "last_name_key", "full_name_key",
    "breed_name", "breed_name_key",
    "voice_pitch", "voice_actor", "voice_effect",
    "skin_tone", "skin_tone_val_shift", "custom_texture", "pelt_layers",
    "physique", "facial_attr", "genetic_data", "flags", "pronouns", "parts_custom_tattoos",
    "outfits", "current_outfit_type", "current_outfit_index",
)


def copy(source, target, names=FIELDS):
    """Copy the fields `names` of the protobuf message `source` that it has
    into `target`, a message of the same type. Fields the game's build does
    not know are skipped."""
    known = source.DESCRIPTOR.fields_by_name
    for name in names:
        field = known.get(name)
        if field is None:
            continue
        value = getattr(source, name)
        repeated = field.label == 3 if hasattr(field, "label") else field.is_repeated
        if repeated:
            target.ClearField(name)
            if field.cpp_type == 10:
                for item in value:
                    getattr(target, name).add().CopyFrom(item)
            else:
                getattr(target, name).extend(value)
        elif field.cpp_type == 10:
            if source.HasField(name):
                getattr(target, name).CopyFrom(value)
        else:
            setattr(target, name, value)


def take(sim_id):
    """Bytes of how the sim `sim_id` looks in our save, b"" for none."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    sim = services.get_persistence_service().get_sim_proto_buff(sim_id)
    if sim is None:
        return b""
    look = serialization.SimData()
    copy(sim, look)
    return look.SerializeToString()


def apply(sim_id, data):
    """Host: put the look `data` (bytes from `take`) on our sim `sim_id`, which
    may stand on the lot, and have every game client shown it. Each step that
    the game refuses is logged and the others still run."""
    import services
    from protocolbuffers import FileSerialization_pb2 as serialization

    sim_info = services.sim_info_manager().get(sim_id)
    if sim_info is None:
        log.info("appearance: no sim {:#x} here".format(sim_id))
        return
    look = serialization.SimData()
    look.MergeFromString(data)
    failed = [name for name, step in _steps(sim_info, look) if not _try(name, step)]
    log.info("appearance: sim {:#x} looks as edited by the other player{}".format(
        sim_id, " (not done: {})".format(", ".join(failed)) if failed else ""))


def _try(name, step):
    try:
        step()
        return True
    except Exception:
        log.exception("appearance: {} failed".format(name))
        return False


def _steps(sim_info, look):
    """(name, step) in the order the game itself changes a sim's look."""
    base = sim_info._base

    def names():
        # Assigned through the sim's fields, so that clients hear of them.
        for name in ("first_name", "last_name"):
            if getattr(look, name):
                setattr(sim_info, name, getattr(look, name))
        for name in ("first_name_key", "last_name_key", "full_name_key"):
            if getattr(look, name, 0):
                setattr(base, name, getattr(look, name))

    def body():
        for name in ("skin_tone", "skin_tone_val_shift", "custom_texture", "voice_pitch", "voice_actor",
                     "voice_effect", "physique", "flags"):
            if hasattr(look, name):
                setattr(base, name, getattr(look, name))
        base.facial_attributes = look.facial_attr
        base.genetic_data = look.genetic_data.SerializeToString()
        base.pelt_layers = look.pelt_layers.SerializeToString()
        if hasattr(look, "pronouns"):
            base.pronouns = look.pronouns.SerializeToString()

    def tattoos():
        if hasattr(look, "parts_custom_tattoos") and hasattr(base, "parts_custom_tattoos"):
            base.parts_custom_tattoos = dict((tattoo.body_type, tattoo.texture_id)
                                             for tattoo in look.parts_custom_tattoos)

    def outfits():
        sim_info.load_outfits(look.outfits)

    def current_outfit():
        outfit = (look.current_outfit_type, look.current_outfit_index)
        if not sim_info.has_outfit(outfit):
            outfit = (0, 0)
        if hasattr(sim_info, "set_outfit_dirty"):
            sim_info.set_outfit_dirty(outfit[0])
        sim_info.set_current_outfit(outfit)
        sim = sim_info.get_sim_instance()
        if sim is not None and hasattr(sim, "on_outfit_changed"):
            try:
                sim.on_outfit_changed(sim_info, outfit)
            except TypeError:
                # Newer builds pass the outfit it changed from too.
                sim.on_outfit_changed(sim_info, outfit, outfit)

    def shown():
        tracker = getattr(sim_info, "appearance_tracker", None)
        if tracker is not None:
            tracker.evaluate_appearance_modifiers()
        sim_info.resend_physical_attributes()
        sim_info.resend_current_outfit()
        if hasattr(sim_info, "force_resend_suntan_data"):
            sim_info.force_resend_suntan_data()

    return (("names", names), ("body", body), ("tattoos", tattoos), ("outfits", outfits),
            ("current outfit", current_outfit), ("shown", shown))
