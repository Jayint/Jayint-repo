#include <bits/stdc++.h>
using namespace std;

int pn[10005];

bool isP(int x){
    string s = to_string(x);
    string rs = s;
    reverse(rs.begin(), rs.end());
    if(s == rs) return true;
    else return false;
}

int PN(int x){
    if(pn[x] != -1) return pn[x];
    if(x == 0) return pn[x] = 0;
    if(x < 10) return pn[x] = 1;
    pn[x] = 0;
    for(int j = 1; j <= x; j++){
        if(isP(j) && PN(x - j) == 0){
            pn[x] = 1;
            break;
        } 
    }
    return pn[x];
}

int main(){
    memset(pn, -1, sizeof(pn));
    for(int i = 1; i <= 100; i++){
        cout << i << ":" << PN(i) << '\n';
    }
}