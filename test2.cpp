#include <bits/stdc++.h>
using namespace std;

const int N = 100;

int sg[N + 1];

int mex(set<int>& s) {
    int g = 0;
    while (s.count(g)) g++;
    return g;
}

int main() {
    sg[0] = 0;

    for (int n = 1; n <= N; n++) {
        set<int> reachable;

        // 操作1：从一堆中取走任意正数个石子
        // n -> 0, 1, ..., n-1
        for (int i = 0; i < n; i++) {
            reachable.insert(sg[i]);
        }

        // 操作2：把一堆拆成两堆非空石子
        // n -> i 和 n-i
        for (int i = 1; i < n; i++) {
            reachable.insert(sg[i] ^ sg[n - i]);
        }

        sg[n] = mex(reachable);
    }

    cout << "n\tSG(n)\n";
    cout << "--------------------------------\n";

    for (int n = 0; n <= N; n++) {
        int guess;
        if (n % 4 == 0) guess = n - 1;
        else if (n % 4 == 3) guess = n + 1;
        else guess = n;

        //if (n == 0) guess = 0;

        cout << n << "\t"
             << sg[n] << '\n';
    }

    return 0;
}