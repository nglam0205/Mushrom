from mtranslate import translate
import sys

def find_between( s, first, last ):
    try:
        start = s.index( first ) + len( first )
        end = s.index( last, start )
        return s[start:end]
    except ValueError:
        return ""

def is_first_char_upper(s):
    return s and s[0].isupper()

file1 = open(sys.argv[1], 'r')
file2 = open(sys.argv[1], 'r')
cont = file2.read()

count = 0

while True:

    line = file1.readline()

    if not line:
        break
    a=find_between(line.strip(), ">", "</" )
    t=translate(a,sys.argv[2])
        
    if is_first_char_upper(a):
        t = t.capitalize()

    temp_a = ">" + a + "</"
    temp_t = ">" + t + "</"
    print(a + " ==> " +t)
    cont = cont.replace(temp_a, temp_t)

file1.close()
file2.close()

with open(sys.argv[1], 'w') as file:

    file.write(cont)


#author BHLNK , Arnaud Alies , stackoverflow.com