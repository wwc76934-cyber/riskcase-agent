"""Frozen, project-owned skill bundles. Loading guidance never changes permissions."""
import json
from pathlib import Path
import re
from app.investigation.contracts import digest


def freeze(root):
    root=Path(root).resolve(); bundles={}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir(): continue
        if folder.is_symlink() or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',folder.name): raise ValueError('Invalid skill folder')
        files={}
        for path in folder.rglob('*'):
            if path.is_symlink() or not path.resolve().is_relative_to(root): raise ValueError('Skill path escape')
            if path.is_file():
                if path.suffix!='.md' or path.stat().st_size>16000: raise ValueError('Unsupported skill resource')
                files[path.relative_to(folder).as_posix()]=path.read_text(encoding='utf-8')
        raw=files['SKILL.md']; parts=raw.split('---',2)
        if len(parts)!=3 or parts[0].strip(): raise ValueError('Skill frontmatter required')
        metadata={}
        # Deliberately narrow YAML subset: string name/description, quoted JSON strings supported.
        # These files remain standard YAML; arbitrary YAML objects/executable tags are not accepted.
        for line in parts[1].strip().splitlines():
            key, value=line.split(':',1);key=key.strip();value=value.strip()
            if key in metadata or key not in ('name','description'): raise ValueError('Unsupported metadata')
            metadata[key]=json.loads(value) if value.startswith('"') else value
        if metadata.get('name')!=folder.name or not 1<=len(metadata.get('description',''))<=1024:
            raise ValueError('Invalid skill metadata')
        bundles[folder.name]=dict(**metadata,body=parts[2].strip(),files=files,version=digest(files))
    if not bundles: raise ValueError('No skills')
    return bundles


def catalog(bundles):
    return [{k:b[k] for k in ('name','description','version')} for b in bundles.values()]


def read(bundles,name,resource='SKILL.md'):
    if name not in bundles or resource not in bundles[name]['files']: raise ValueError('Unknown frozen skill resource')
    bundle=bundles[name]
    if digest(bundle['files'])!=bundle['version']: raise ValueError('Frozen skill changed')
    return dict(name=name,version=bundle['version'],resource=resource,
                text=bundle['body'] if resource=='SKILL.md' else bundle['files'][resource],grants_permissions=False)
