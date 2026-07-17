#include <bits/stdc++.h>
using namespace std;
#define ll long long 

int function1(int x){
    int k = 2, b = 1;
    return k * x + b;
}
int main()
{
    int x;
    cin >> x;
    int y = function1(x);
    cout << y << '\n';
    return 0;
}
    