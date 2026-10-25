#!/usr/bin/env python3
"""Exercise Weston's actual SHM copy routine for both output origins, without GL."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def function(source, name):
    start = source.index(name + '(')
    body = source.index('{', start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def check(source_path):
    source = source_path.read_text()
    reverse = re.search(r'gl_task->reverse\s*=\s*(.*?);', source, re.S).group(1)
    copy = function(source, 'copy_capture')
    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define GL_PIXEL_PACK_BUFFER 1
#define GL_MAP_READ_BIT 1
#define EXTENSION_ANGLE_PACK_REVERSE_ROW_ORDER 1
static uint8_t pixels[12], destination[12];
struct wl_shm_buffer { int unused; };
struct weston_buffer { struct wl_shm_buffer *shm_buffer; };
struct gl_renderer {
    bool angle;
    void *(*map_buffer_range)(int, int, int, int);
    void (*unmap_buffer)(int);
};
struct gl_output_state { float y_flip; };
struct gl_capture_task {
    struct weston_buffer *task;
    struct gl_renderer *gr;
    int pbo, stride, height;
    bool reverse;
};
static bool is_y_flipped(struct gl_output_state *go) { return go->y_flip < 0; }
static bool gl_extensions_has(struct gl_renderer *gr, int unused) { return gr->angle; }
static void glBindBuffer(int unused, int ignored) {}
static struct weston_buffer *weston_capture_task_get_buffer(struct weston_buffer *buffer) { return buffer; }
static void *wl_shm_buffer_get_data(struct wl_shm_buffer *shm) { return destination; }
static void wl_shm_buffer_begin_access(struct wl_shm_buffer *shm) {}
static void wl_shm_buffer_end_access(struct wl_shm_buffer *shm) {}
static void *map(int target, int offset, int length, int flags) { return pixels; }
static void unmap(int unused) {}
static void
''' + copy + r'''
int main(void) {
    struct gl_renderer renderer = {.map_buffer_range = map, .unmap_buffer = unmap};
    struct gl_renderer *gr = &renderer;
    struct gl_output_state output;
    struct gl_output_state *go = &output;
    struct wl_shm_buffer shm;
    struct weston_buffer buffer = {.shm_buffer = &shm};
    struct gl_capture_task task = {.task = &buffer, .gr = gr, .stride = 4, .height = 3};
    for (int origin = 0; origin < 2; origin++) {
        go->y_flip = origin ? -1 : 1;
        for (int angle = 0; angle < 2; angle++) {
            gr->angle = angle;
            /* Emulate glReadPixels row order. The ANGLE pack flag is set
             * only for an output that needs flipping, as in Weston's caller. */
            for (int row = 0; row < 3; row++)
                memset(pixels + 4 * row, (origin && !angle) ? 3 - row : row + 1, 4);
            memset(destination, 0, sizeof destination);
            task.reverse = ''' + reverse + r''';
            copy_capture(&task);
            printf("%d %d %d %d %d\n", origin, angle,
                   destination[0], destination[4], destination[8]);
        }
    }
}
'''
    with tempfile.TemporaryDirectory(prefix='licasa-weston-rows-') as temporary:
        directory = Path(temporary)
        (directory / 'rows.c').write_text(harness)
        subprocess.run(['cc', '-std=c11', '-Werror', str(directory / 'rows.c'),
                        '-o', str(directory / 'rows')], check=True)
        output = subprocess.check_output([str(directory / 'rows')], text=True)
    cases = []
    for line in output.splitlines():
        origin, angle, *rows = map(int, line.split())
        cases.append({'output': 'window' if origin else 'surfaceless',
                      'angle_pack_reverse_row_order': bool(angle),
                      'rows': rows, 'correct_orientation': rows == [1, 2, 3]})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    cases = check(args.source)
    print(json.dumps(cases, indent=2))
    raise SystemExit(0 if all(case['correct_orientation'] for case in cases) else 1)


if __name__ == '__main__':
    main()
