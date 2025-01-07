![header](https://capsule-render.vercel.app/api?type=waving&height=250&color=timeGradient&text=Modpack%20CLI%20Tool&fontAlignY=46&animation=fadeIn)

> [!WARNING]  
> This tool is made for the sole purpose of automating stuff when i develop my modpacks. It is therefore not made to be user friendly or flexible in any way as it requires a very specific workflow to function.
> Long story short, i do not recommend that anyone else uses this tool due to the reasons above.

## Breakneck specific stuff

When migrating to a new Minecraft version (*For example, going from 1.21.3 to 1.21.4*), make sure to leave only the last changelog in the repository from the previous version like seen below. 
This is due to the program reading the files and thereby being able to recognize that it should compare the first new version to that old one.
This feature is only active when enabling the `breakneck-fixes` option.

![1736264492349](image/README/1736264492349.png)