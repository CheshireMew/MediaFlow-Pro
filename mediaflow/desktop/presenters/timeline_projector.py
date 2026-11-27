from __future__ import annotations

from collections.abc import Sequence

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.desktop.component_contexts import TimelineProjectorContext
from mediaflow.desktop.presentation_messages import (
    system_name,
)
from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, CompoundClip, Transition

from .base import Projector
from .timeline_rows import project_clip_row


class TimelineProjector(Projector[TimelineProjectorContext]):
    def __init__(self, context: TimelineProjectorContext):
        super().__init__(context)
        self._clips_by_id: dict[str, Clip] = {}
        self._compounds_by_id: dict[str, CompoundClip] = {}
        self._compound_by_clip_id: dict[str, str] = {}
        self._transitions_by_id: dict[str, Transition] = {}
        self._transition_ids_by_clip: dict[str, set[str]] = {}

    def _clear_relation_index(self) -> None:
        self._clips_by_id = {}
        self._compounds_by_id = {}
        self._compound_by_clip_id = {}
        self._transitions_by_id = {}
        self._transition_ids_by_clip = {}

    def _index_relations(self, state) -> None:
        self._clips_by_id = {clip.id: clip for clip in state.clips}
        self._compounds_by_id = {compound.id: compound for compound in state.compounds}
        self._compound_by_clip_id = {
            clip_id: compound.id for compound in state.compounds for clip_id in compound.clip_ids
        }
        self._transitions_by_id = {item.id: item for item in state.transitions}
        transition_ids: dict[str, set[str]] = {}
        for item in state.transitions:
            transition_ids.setdefault(item.left_clip_id, set()).add(item.id)
            transition_ids.setdefault(item.right_clip_id, set()).add(item.id)
        self._transition_ids_by_clip = transition_ids

    def refresh_sequences(self) -> None:
        if not self._context.state.binding.current:
            self._context.models.sequences.set_items([])
            return
        current = self._context.state.binding.require_current()
        sequences = current.list_sequences()
        by_id = {item.id: item for item in sequences}
        variants = {item.sequence_id: item for item in current.list_sequence_variants()}
        source_revisions = {
            item.source_sequence_id: (
                by_id[item.source_sequence_id].timeline_revision
                if item.source_sequence_id in by_id
                else current.get_sequence(item.source_sequence_id).timeline_revision
            )
            for item in variants.values()
        }
        self._context.models.sequences.set_items(
            [
                {
                    "sequenceId": sequence.id,
                    "name": sequence.name,
                    "displayName": (
                        f"{system_name(sequence.name)} · 需更新"
                        if sequence.id in variants
                        and variants[sequence.id].source_timeline_revision
                        != source_revisions[variants[sequence.id].source_sequence_id]
                        else system_name(sequence.name)
                    ),
                    "kind": sequence.kind.value,
                    "profile": f"{sequence.profile.width}×{sequence.profile.height}",
                    "colorMode": sequence.profile.color_mode.value,
                    "sourceSequenceId": (
                        variants[sequence.id].source_sequence_id if sequence.id in variants else ""
                    ),
                    "variantPreset": (variants[sequence.id].preset_id if sequence.id in variants else ""),
                    "variantStale": (
                        sequence.id in variants
                        and variants[sequence.id].source_timeline_revision
                        != source_revisions[variants[sequence.id].source_sequence_id]
                    ),
                }
                for sequence in sequences
            ]
        )

    def refresh_timeline(
        self,
        *,
        defer_clip_updates: bool = False,
        schedule_preview: bool = True,
    ) -> None:
        self._context.invalidate_audio_metrics()
        if not self._context.state.binding.timeline or not self._context.state.binding.current:
            self._clear_relation_index()
            self._context.models.tracks.set_items([])
            self._context.models.clips.set_items([])
            self._context.models.visible_clips.set_source_items([])
            self._context.models.compound_clips.set_items([])
            self._context.models.transitions.set_items([])
            self._context.models.markers.set_items([])
            self._context.models.ranges.set_items([])
            self._context.updates.commit(export_capability=True)
            return
        state = self._context.state.binding.require_timeline().state
        assets = {asset.id: asset for asset in self._context.state.binding.require_current().list_assets()}
        tracks_by_id = {track.id: track for track in state.tracks}
        track_positions = {track.id: index for index, track in enumerate(state.tracks)}
        self._index_relations(state)
        compound_by_clip_id = self._compound_by_clip_id
        self._context.models.tracks.set_items(
            [
                {
                    "trackId": track.id,
                    "name": track.name,
                    "displayName": system_name(track.name),
                    "kind": track.kind.value,
                    "position": track.position,
                    "enabled": track.enabled,
                    "locked": track.locked,
                    "muted": track.muted,
                    "solo": track.solo,
                    "audioBusId": track.audio_bus_id or "",
                    "linkedAudioTrackId": track.linked_audio_track_id or "",
                    "primaryDialogue": track.primary_dialogue,
                }
                for track in state.tracks
            ]
        )
        clip_rows = [
            self._project_clip_row(
                clip,
                asset=assets[clip.asset_id],
                source_track=tracks_by_id[clip.track_id],
                track_positions=track_positions,
                compound_id=compound_by_clip_id.get(clip.id, ""),
            )
            for clip in state.clips
        ]
        if defer_clip_updates:
            self._context.models.clips.set_items_deferred(clip_rows)
        else:
            self._context.models.clips.set_items(clip_rows)
        self._context.models.visible_clips.set_source_items(clip_rows)
        clips_by_id = {clip.id: clip for clip in state.clips}
        compound_rows = []
        for compound in state.compounds:
            members = [clips_by_id[clip_id] for clip_id in compound.clip_ids]
            first = members[0]
            compound_rows.append(
                {
                    "compoundId": compound.id,
                    "name": compound.name,
                    "primaryClipId": first.id,
                    "memberClipIds": list(compound.clip_ids),
                    "memberCount": len(members),
                    "trackId": first.track_id,
                    "trackPosition": track_positions[first.track_id],
                    "trackKind": tracks_by_id[first.track_id].kind.value,
                    "startFrame": first.timeline_start,
                    "endFrame": members[-1].timeline_end,
                    "durationFrames": members[-1].timeline_end - first.timeline_start,
                    "hasAudio": any(assets[clip.asset_id].metadata.has_audio for clip in members),
                }
            )
        self._context.models.compound_clips.set_items(compound_rows)
        available_clip_ids = {item["clipId"] for item in clip_rows}
        self._context.state.selection.clip_ids = [
            clip_id for clip_id in self._context.state.selection.clip_ids if clip_id in available_clip_ids
        ]
        available_compound_ids = {item["compoundId"] for item in compound_rows}
        if self._context.state.selection.compound_id not in available_compound_ids:
            self._context.state.selection.compound_id = ""
        self._context.models.visible_clips.set_selected_ids(self._context.state.selection.clip_ids)
        clips = clips_by_id
        self._context.models.transitions.set_items(
            [
                {
                    "transitionId": item.id,
                    "trackId": item.track_id,
                    "trackPosition": track_positions[item.track_id],
                    "leftClipId": item.left_clip_id,
                    "rightClipId": item.right_clip_id,
                    "kind": item.kind.value,
                    "durationFrames": item.duration,
                    "boundaryFrame": clips[item.left_clip_id].timeline_end,
                    "internalToCompound": bool(
                        compound_by_clip_id.get(item.left_clip_id)
                        and compound_by_clip_id.get(item.left_clip_id)
                        == compound_by_clip_id.get(item.right_clip_id)
                    ),
                }
                for item in state.transitions
            ]
        )
        self._context.models.markers.set_items(
            [
                {
                    "markerId": item.id,
                    "frame": item.frame,
                    "name": item.name,
                    "markerColor": item.color,
                }
                for item in state.markers
            ]
        )
        self._context.models.ranges.set_items(
            [
                {
                    "rangeId": item.id,
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "name": item.name,
                    "rangeColor": item.color,
                }
                for item in state.ranges
            ]
        )
        self._context.updates.commit(export_capability=True)
        if schedule_preview:
            self._context.schedule_preview(clip_count=len(state.clips))

    def refresh_clip_rows(
        self,
        clip_ids: list[str],
        *,
        clips: Sequence[Clip] | None = None,
        defer_updates: bool = True,
        refresh_relations: bool = True,
        schedule_preview: bool = True,
    ) -> None:
        """Refresh edited clip rows without rebuilding every projected clip."""

        if not self._context.state.binding.timeline or not self._context.state.binding.current:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        wanted = set(clip_ids)
        if not wanted:
            return
        state = self._context.state.binding.require_timeline().state
        changed_clips = (
            {clip.id: clip for clip in clips}
            if clips is not None
            else {clip_id: self._clips_by_id[clip_id] for clip_id in wanted if clip_id in self._clips_by_id}
        )
        if set(changed_clips) != wanted:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        tracks = sorted(state.tracks, key=lambda item: (item.position, item.id))
        tracks_by_id = {track.id: track for track in tracks}
        track_positions = {track.id: index for index, track in enumerate(tracks)}
        current_compounds = {compound.id: tuple(compound.clip_ids) for compound in state.compounds}
        projected_compounds = {
            compound.id: tuple(compound.clip_ids) for compound in self._compounds_by_id.values()
        }
        if current_compounds != projected_compounds:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        if len(tracks) != self._context.models.tracks.rowCount() or any(
            self._context.models.tracks.findRow("trackId", track.id) < 0 for track in tracks
        ):
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        asset_rows: dict[str, Asset] | None = None
        compound_by_clip_id = self._compound_by_clip_id
        changed_rows = []
        for clip_id in clip_ids:
            clip = changed_clips[clip_id]
            row_index = self._context.models.clips.findRow("clipId", clip_id)
            before = self._context.models.clips.get(row_index)
            if not before:
                self.refresh_timeline(
                    defer_clip_updates=defer_updates,
                    schedule_preview=schedule_preview,
                )
                return
            if before["assetId"] != clip.asset_id:
                if asset_rows is None:
                    asset_rows = {
                        asset.id: asset
                        for asset in self._context.state.binding.require_current().list_assets()
                    }
                asset = asset_rows[clip.asset_id]
                asset_name = asset.name
                asset_kind = asset.kind.value
                has_audio = asset.metadata.has_audio
                waveform_ready = bool(asset.waveform_path)
            else:
                asset_name = before["assetName"]
                asset_kind = before["assetKind"]
                has_audio = before["hasAudio"]
                waveform_ready = before["waveformReady"]
            source_track = tracks_by_id[clip.track_id]
            audio_track_position = -1
            if (
                clip.media_kind == ClipMediaKind.LINKED_AV
                and source_track.linked_audio_track_id in track_positions
            ):
                audio_track_position = track_positions[source_track.linked_audio_track_id]
            changed_rows.append(
                project_clip_row(
                    clip,
                    asset_fields={
                        **before,
                        "assetName": asset_name,
                        "assetKind": asset_kind,
                        "hasAudio": has_audio,
                        "waveformReady": waveform_ready,
                    },
                    track_kind=source_track.kind.value,
                    track_position=track_positions[clip.track_id],
                    audio_track_position=audio_track_position,
                    filmstrip_frames=self._context.state.presentation.filmstrip_frames.get(clip.id, []),
                    compound_id=compound_by_clip_id.get(clip.id, ""),
                )
            )
        if not self._context.models.clips.update_items_by_key(
            changed_rows,
            deferred=defer_updates,
        ):
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        if not self._context.models.visible_clips.update_source_items(changed_rows):
            self._context.models.visible_clips.set_source_items(self._context.models.clips.snapshot())
        self._clips_by_id.update(changed_clips)
        self._context.update_snapping_clips(
            changed_clips.values(),
            state=state,
        )
        if refresh_relations:
            self._refresh_clip_relations(wanted, track_positions, tracks_by_id)
        self._context.updates.commit(export_capability=True)
        if schedule_preview:
            self._context.schedule_preview(clip_count=len(self._clips_by_id))

    def refresh_clip_structure(
        self,
        *,
        schedule_preview: bool = True,
    ) -> None:
        """Project a small clip add/remove delta without rebuilding 5,000 rows."""

        if not self._context.state.binding.timeline or not self._context.state.binding.current:
            self.refresh_timeline(schedule_preview=schedule_preview)
            return
        state = self._context.state.binding.require_timeline().state
        tracks = list(state.tracks)
        track_positions = {track.id: index for index, track in enumerate(tracks)}
        tracks_by_id = {track.id: track for track in tracks}
        if (
            [row["trackId"] for row in self._context.models.tracks.snapshot()]
            != [track.id for track in tracks]
            or {item.id: item for item in state.compounds} != self._compounds_by_id
            or {item.id: item for item in state.transitions} != self._transitions_by_id
            or [row["markerId"] for row in self._context.models.markers.snapshot()]
            != [item.id for item in state.markers]
            or [row["rangeId"] for row in self._context.models.ranges.snapshot()]
            != [item.id for item in state.ranges]
        ):
            self.refresh_timeline(schedule_preview=schedule_preview)
            return

        current_clips = {clip.id: clip for clip in state.clips}
        removed_ids = set(self._clips_by_id) - set(current_clips)
        changed_clips = {
            clip_id: clip for clip_id, clip in current_clips.items() if clip != self._clips_by_id.get(clip_id)
        }
        if not removed_ids and not changed_clips:
            self._context.updates.commit(export_capability=True)
            if schedule_preview:
                self._context.schedule_preview(clip_count=len(self._clips_by_id))
            return

        assets = {asset.id: asset for asset in self._context.state.binding.require_current().list_assets()}
        changed_rows = [
            self._project_clip_row(
                clip,
                asset=assets[clip.asset_id],
                source_track=tracks_by_id[clip.track_id],
                track_positions=track_positions,
            )
            for clip in changed_clips.values()
        ]
        ordered_ids = [clip.id for clip in state.clips]
        if not self._context.models.clips.patch_items_by_key(
            changed_rows,
            removed_keys=removed_ids,
            ordered_keys=ordered_ids,
        ):
            self.refresh_timeline(schedule_preview=schedule_preview)
            return
        if not self._context.models.visible_clips.patch_source_items(
            changed_rows,
            removed_keys=removed_ids,
            ordered_keys=ordered_ids,
            selected_ids=self._context.state.selection.clip_ids,
        ):
            self._context.models.visible_clips.set_source_items(self._context.models.clips.snapshot())

        self._context.invalidate_audio_metrics()
        self._clips_by_id = current_clips
        self._context.update_snapping_clips(
            changed_clips.values(),
            removed_ids,
            state=state,
        )
        available_clip_ids = set(current_clips)
        self._context.state.selection.clip_ids = [
            clip_id for clip_id in self._context.state.selection.clip_ids if clip_id in available_clip_ids
        ]
        self._context.models.visible_clips.set_selected_ids(self._context.state.selection.clip_ids)
        self._refresh_clip_relations(
            set(changed_clips) | removed_ids,
            track_positions,
            tracks_by_id,
        )
        self._context.updates.commit(export_capability=True)
        if schedule_preview:
            self._context.schedule_preview(clip_count=len(current_clips))

    def refresh_known_clip_membership(
        self,
        changed_clips: Sequence[Clip],
        *,
        removed_clip_ids: set[str],
        row_templates: dict[str, dict],
        schedule_preview: bool = True,
    ) -> None:
        """Project a controller-known membership delta without loading the full timeline."""

        changed_by_id = {clip.id: clip for clip in changed_clips}
        affected_ids = set(changed_by_id) | removed_clip_ids
        if (
            not self._context.state.binding.timeline
            or not self._context.state.binding.current
            or set(changed_by_id) != set(row_templates)
            or any(
                clip_id in self._compound_by_clip_id or self._transition_ids_by_clip.get(clip_id)
                for clip_id in affected_ids
            )
        ):
            self.refresh_timeline(schedule_preview=schedule_preview)
            return
        current_clips = dict(self._clips_by_id)
        for clip_id in removed_clip_ids:
            current_clips.pop(clip_id, None)
        current_clips.update(changed_by_id)
        changed_rows = [
            self._project_clip_row_from_template(
                clip,
                row_templates[clip.id],
            )
            for clip in changed_clips
        ]
        ordered_ids = [
            clip.id
            for clip in sorted(
                current_clips.values(),
                key=lambda item: (item.timeline_start, item.id),
            )
        ]
        if not self._context.models.clips.patch_items_by_key(
            changed_rows,
            removed_keys=removed_clip_ids,
            ordered_keys=ordered_ids,
        ):
            self.refresh_timeline(schedule_preview=schedule_preview)
            return
        if not self._context.models.visible_clips.patch_source_items(
            changed_rows,
            removed_keys=removed_clip_ids,
            ordered_keys=ordered_ids,
            selected_ids=self._context.state.selection.clip_ids,
        ):
            self._context.models.visible_clips.set_source_items(self._context.models.clips.snapshot())
        self._context.invalidate_audio_metrics()
        self._clips_by_id = current_clips
        self._context.invalidate_snapping()
        self._context.updates.commit(export_capability=True)
        if schedule_preview:
            self._context.schedule_preview(clip_count=len(current_clips))

    def _project_clip_row_from_template(
        self,
        clip: Clip,
        template: dict,
    ) -> dict:
        track_position = self._context.models.tracks.findRow(
            "trackId",
            clip.track_id,
        )
        track_row = self._context.models.tracks.get(track_position)
        if not track_row or template.get("assetId") != clip.asset_id:
            raise ValueError("Known clip membership projection lost its source row")
        audio_track_position = -1
        linked_audio_track_id = str(track_row["linkedAudioTrackId"])
        if clip.media_kind == ClipMediaKind.LINKED_AV and linked_audio_track_id:
            audio_track_position = self._context.models.tracks.findRow(
                "trackId",
                linked_audio_track_id,
            )
        return project_clip_row(
            clip,
            asset_fields=template,
            track_kind=str(track_row["kind"]),
            track_position=track_position,
            audio_track_position=audio_track_position,
            filmstrip_frames=template["filmstripFrames"] if str(template["clipId"]) == clip.id else [],
        )

    def _project_clip_row(
        self,
        clip: Clip,
        *,
        asset: Asset,
        source_track,
        track_positions: dict[str, int],
        compound_id: str = "",
    ) -> dict:
        audio_track_position = -1
        if (
            clip.media_kind == ClipMediaKind.LINKED_AV
            and source_track.linked_audio_track_id in track_positions
        ):
            audio_track_position = track_positions[source_track.linked_audio_track_id]
        return project_clip_row(
            clip,
            asset_fields={
                "assetName": asset.name,
                "assetKind": asset.kind.value,
                "hasAudio": asset.metadata.has_audio,
                "waveformReady": bool(asset.waveform_path),
            },
            track_kind=source_track.kind.value,
            track_position=track_positions[clip.track_id],
            audio_track_position=audio_track_position,
            filmstrip_frames=self._context.state.presentation.filmstrip_frames.get(clip.id, []),
            compound_id=compound_id,
        )

    def _refresh_clip_relations(self, changed_ids, track_positions, tracks_by_id) -> None:
        compound_ids = {
            compound_id
            for clip_id in changed_ids
            if (compound_id := self._compound_by_clip_id.get(clip_id)) is not None
        }
        compound_rows = []
        for compound_id in compound_ids:
            compound = self._compounds_by_id[compound_id]
            members = [self._clips_by_id[clip_id] for clip_id in compound.clip_ids]
            first = members[0]
            compound_rows.append(
                {
                    "compoundId": compound.id,
                    "name": compound.name,
                    "primaryClipId": first.id,
                    "memberClipIds": list(compound.clip_ids),
                    "memberCount": len(members),
                    "trackId": first.track_id,
                    "trackPosition": track_positions[first.track_id],
                    "trackKind": tracks_by_id[first.track_id].kind.value,
                    "startFrame": first.timeline_start,
                    "endFrame": members[-1].timeline_end,
                    "durationFrames": members[-1].timeline_end - first.timeline_start,
                    "hasAudio": any(
                        bool(
                            self._context.models.clips.get(
                                self._context.models.clips.findRow("clipId", item.id)
                            )["hasAudio"]
                        )
                        for item in members
                    ),
                }
            )
        if compound_rows and not self._context.models.compound_clips.update_items_by_key(compound_rows):
            self.refresh_timeline()
            return
        transition_ids = {
            transition_id
            for clip_id in changed_ids
            for transition_id in self._transition_ids_by_clip.get(clip_id, set())
        }
        invalid_transition_ids = {
            transition_id
            for transition_id in transition_ids
            if (
                (item := self._transitions_by_id.get(transition_id)) is None
                or not TimelineRules.transition_is_valid(item, self._clips_by_id)
            )
        }
        if invalid_transition_ids:
            self._context.models.transitions.set_items(
                [
                    row
                    for row in self._context.models.transitions.snapshot()
                    if row["transitionId"] not in invalid_transition_ids
                ]
            )
            for transition_id in invalid_transition_ids:
                item = self._transitions_by_id.pop(transition_id, None)
                if item is None:
                    continue
                for clip_id in (item.left_clip_id, item.right_clip_id):
                    self._transition_ids_by_clip.get(clip_id, set()).discard(transition_id)
        transition_ids -= invalid_transition_ids
        transition_rows = [
            {
                "transitionId": item.id,
                "trackId": item.track_id,
                "trackPosition": track_positions[item.track_id],
                "leftClipId": item.left_clip_id,
                "rightClipId": item.right_clip_id,
                "kind": item.kind.value,
                "durationFrames": item.duration,
                "boundaryFrame": self._clips_by_id[item.left_clip_id].timeline_end,
                "internalToCompound": bool(
                    self._compound_by_clip_id.get(item.left_clip_id)
                    and self._compound_by_clip_id.get(item.left_clip_id)
                    == self._compound_by_clip_id.get(item.right_clip_id)
                ),
            }
            for transition_id in transition_ids
            if (item := self._transitions_by_id.get(transition_id)) is not None
        ]
        if transition_rows and not self._context.models.transitions.update_items_by_key(transition_rows):
            self.refresh_timeline()
