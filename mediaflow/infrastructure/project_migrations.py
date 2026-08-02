from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .project_migrations_v1_v8 import (
    migrate_v1_to_v2,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    migrate_v5_to_v6,
    migrate_v6_to_v7,
    migrate_v7_to_v8,
    migrate_v8_to_v9,
)
from .project_migrations_v9_v16 import (
    migrate_v9_to_v10,
    migrate_v10_to_v11,
    migrate_v11_to_v12,
    migrate_v12_to_v13,
    migrate_v13_to_v14,
    migrate_v14_to_v15,
    migrate_v15_to_v16,
    migrate_v16_to_v17,
)
from .project_migrations_v17_v24 import (
    migrate_v17_to_v18,
    migrate_v18_to_v19,
    migrate_v19_to_v20,
    migrate_v20_to_v21,
    migrate_v21_to_v22,
    migrate_v22_to_v23,
    migrate_v23_to_v24,
    migrate_v24_to_v25,
)
from .project_migrations_v25_v32 import (
    migrate_v25_to_v26,
    migrate_v26_to_v27,
    migrate_v27_to_v28,
    migrate_v28_to_v29,
    migrate_v29_to_v30,
    migrate_v30_to_v31,
    migrate_v31_to_v32,
    migrate_v32_to_v33,
)
from .project_migrations_v33_v40 import (
    migrate_v33_to_v34,
    migrate_v34_to_v35,
    migrate_v35_to_v36,
    migrate_v36_to_v37,
    migrate_v37_to_v38,
)


@dataclass(frozen=True, slots=True)
class ProjectMigration:
    source_version: int
    target_version: int
    apply: Callable[[object], None]


PROJECT_MIGRATIONS = (
    ProjectMigration(1, 2, migrate_v1_to_v2),
    ProjectMigration(2, 3, migrate_v2_to_v3),
    ProjectMigration(3, 4, migrate_v3_to_v4),
    ProjectMigration(4, 5, migrate_v4_to_v5),
    ProjectMigration(5, 6, migrate_v5_to_v6),
    ProjectMigration(6, 7, migrate_v6_to_v7),
    ProjectMigration(7, 8, migrate_v7_to_v8),
    ProjectMigration(8, 9, migrate_v8_to_v9),
    ProjectMigration(9, 10, migrate_v9_to_v10),
    ProjectMigration(10, 11, migrate_v10_to_v11),
    ProjectMigration(11, 12, migrate_v11_to_v12),
    ProjectMigration(12, 13, migrate_v12_to_v13),
    ProjectMigration(13, 14, migrate_v13_to_v14),
    ProjectMigration(14, 15, migrate_v14_to_v15),
    ProjectMigration(15, 16, migrate_v15_to_v16),
    ProjectMigration(16, 17, migrate_v16_to_v17),
    ProjectMigration(17, 18, migrate_v17_to_v18),
    ProjectMigration(18, 19, migrate_v18_to_v19),
    ProjectMigration(19, 20, migrate_v19_to_v20),
    ProjectMigration(20, 21, migrate_v20_to_v21),
    ProjectMigration(21, 22, migrate_v21_to_v22),
    ProjectMigration(22, 23, migrate_v22_to_v23),
    ProjectMigration(23, 24, migrate_v23_to_v24),
    ProjectMigration(24, 25, migrate_v24_to_v25),
    ProjectMigration(25, 26, migrate_v25_to_v26),
    ProjectMigration(26, 27, migrate_v26_to_v27),
    ProjectMigration(27, 28, migrate_v27_to_v28),
    ProjectMigration(28, 29, migrate_v28_to_v29),
    ProjectMigration(29, 30, migrate_v29_to_v30),
    ProjectMigration(30, 31, migrate_v30_to_v31),
    ProjectMigration(31, 32, migrate_v31_to_v32),
    ProjectMigration(32, 33, migrate_v32_to_v33),
    ProjectMigration(33, 34, migrate_v33_to_v34),
    ProjectMigration(34, 35, migrate_v34_to_v35),
    ProjectMigration(35, 36, migrate_v35_to_v36),
    ProjectMigration(36, 37, migrate_v36_to_v37),
    ProjectMigration(37, 38, migrate_v37_to_v38),
)

MIGRATION_BY_SOURCE_VERSION = {migration.source_version: migration for migration in PROJECT_MIGRATIONS}
