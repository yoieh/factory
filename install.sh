#!/bin/sh
set -eu

usage() {
    echo "usage: sh install.sh [--prefix <dir>]"
    echo "Copies the core to \$FACTORY_HOME/core/<version>/, points core/current at it"
    echo "and writes the factory command into <dir> (default ~/.local/bin)."
}

prefix="$HOME/.local/bin"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            [ "$#" -ge 2 ] || { echo "install.sh: --prefix needs a folder" >&2; exit 2; }
            prefix="$2"
            shift 2
            ;;
        --prefix=*)
            prefix="${1#--prefix=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$prefix" in
    "~"/*) prefix="$HOME/${prefix#"~/"}" ;;
esac

source_dir=$(cd "$(dirname "$0")" && pwd)
version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$source_dir/factory/__init__.py")
[ -n "$version" ] || { echo "install.sh: no VERSION in $source_dir/factory/__init__.py" >&2; exit 1; }

home_dir="${FACTORY_HOME:-$HOME/.factory}"
case "$home_dir" in
    /*) ;;
    *) echo "install.sh: FACTORY_HOME must be an absolute path" >&2; exit 1 ;;
esac

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "install.sh: Python 3.10 or newer is required" >&2
    exit 1
}

mkdir -p "$home_dir/core"
chmod 700 "$home_dir"
target="$home_dir/core/$version"
staging="$home_dir/core/.staging-$version-$$"
rm -rf "$staging"
mkdir -p "$staging"
for part in bin factory catalogue skills agents examples tests .claude-plugin install.sh README.md; do
    if [ -e "$source_dir/$part" ]; then
        cp -R "$source_dir/$part" "$staging/$part"
    fi
done
find "$staging" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod 755 "$staging/bin/factory"
rm -rf "$target"
mv "$staging" "$target"
rm -f "$home_dir/core/current"
ln -s "$version" "$home_dir/core/current"

mkdir -p "$prefix"
shim="$prefix/factory"
cat > "$shim" <<EOF
#!/bin/sh
core="\${FACTORY_CORE:-\${FACTORY_HOME:-$home_dir}/core/current}"
exec "\$core/bin/factory" "\$@"
EOF
chmod 755 "$shim"

echo "installed factory $version"
echo "  core: $target"
echo "  command: $shim"
case ":$PATH:" in
    *":$prefix:"*) ;;
    *) echo "  $prefix is not on PATH; add it to your shell profile" ;;
esac
