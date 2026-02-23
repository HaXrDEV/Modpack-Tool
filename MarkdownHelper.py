import re


def remove_bracketed_text(input_str):
    """Remove text surrounded by (), [] or {} from the input string."""
    pattern = r'\(.*?\)|\[.*?\]|\{.*?\}'
    return re.sub(pattern, '', input_str).strip()


def codify_bracketed_text(input_str, keep_brackets=False):
    """Format square-bracketed text as Markdown code."""
    if keep_brackets:
        return re.sub(r'(\[)([^\]]+)(\])', r'`\1\2\3`', input_str)
    return re.sub(r'(?<!\\)\[([^\]]+)\]', r'`\1`', input_str)


def markdown_list_maker(lines):
    """Format an iterable of strings as a Markdown list."""
    return "\n".join(f"- {line}" for line in lines)


def write_differences_to_markdown(differences, input_modpack_name, version1, version2, output_file=None):
    markdown_lines = []

    # Title for the Markdown report
    markdown_lines.append(f"# {input_modpack_name} {version1} -> {version2}\n")

    # Added section
    if differences['added']:
        markdown_lines.append("## Added\n")
        for name in differences['added']:
            markdown_lines.append(f"- {(name)}")
    else:
        markdown_lines.append("## Added\n- None")

    # Removed section
    if differences['removed']:
        markdown_lines.append("## Removed\n")
        for name in differences['removed']:
            markdown_lines.append(f"- {(name)}")
    else:
        markdown_lines.append("## Removed\n- None")

    # Modified section
    if differences['modified']:
        markdown_lines.append("## Modified\n")
        for name, old_version, new_version in differences['modified']:
            markdown_lines.append(f"- **{(name)}**: Changed from `{old_version}` to `{new_version}`")
    else:
        markdown_lines.append("## Modified\n- None")

    # Join all lines into a single string
    markdown_output = "\n".join(markdown_lines)

    # Write to a file if an output path is provided
    if output_file:
        with open(output_file, 'w', encoding='utf8') as f:
            f.write(markdown_output)

    return markdown_output
