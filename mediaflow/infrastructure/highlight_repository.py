from __future__ import annotations

from mediaflow.domain.highlights import HighlightCandidate

from .project_repository_component import ProjectRepositoryComponent


class HighlightRepository(ProjectRepositoryComponent):
    def save_highlights(self, candidates: list[HighlightCandidate]) -> None:
        project = self._owner.catalog.get_project()
        if any(candidate.project_id != project.id for candidate in candidates):
            raise ValueError("Highlight belongs to another project")
        with self.transaction() as connection:
            for candidate in candidates:
                connection.execute(
                    """INSERT INTO highlight_candidate(
                        id, project_id, asset_id, document_id, sequence_id,
                        start_frame, end_frame, title, reason, score, selected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        document_id=excluded.document_id,
                        sequence_id=excluded.sequence_id,
                        start_frame=excluded.start_frame, end_frame=excluded.end_frame,
                        title=excluded.title, reason=excluded.reason,
                        score=excluded.score, selected=excluded.selected""",
                    (
                        candidate.id,
                        candidate.project_id,
                        candidate.asset_id,
                        candidate.document_id,
                        candidate.sequence_id,
                        candidate.start_frame,
                        candidate.end_frame,
                        candidate.title,
                        candidate.reason,
                        candidate.score,
                        int(candidate.selected),
                    ),
                )
            self._touch_project(connection)

    def list_highlights(self, asset_id: str | None = None) -> list[HighlightCandidate]:
        if asset_id:
            rows = self._fetchall(
                "SELECT * FROM highlight_candidate WHERE asset_id=? ORDER BY score DESC, start_frame",
                (asset_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM highlight_candidate ORDER BY score DESC, start_frame")
        return [
            HighlightCandidate(
                id=row["id"],
                project_id=row["project_id"],
                asset_id=row["asset_id"],
                document_id=row["document_id"],
                sequence_id=row["sequence_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                title=row["title"],
                reason=row["reason"],
                score=row["score"],
                selected=bool(row["selected"]),
            )
            for row in rows
        ]

    def delete_highlight(self, candidate_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM highlight_candidate WHERE id=?",
                (candidate_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(candidate_id)
            self._touch_project(connection)
